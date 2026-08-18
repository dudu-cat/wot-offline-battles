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

import server_world
from descriptor_projection import DescriptorStore
from server_battle_authority import SERVER_AUTHORITY_ID, ServerBattleAuthority
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
    "projectile_launch", "projectile_progress", "projectile_resolve",
    "rules_state", "destructible", "descriptor_bundle",
    "destructible_map",
    "battle_result", "leave_battle", "battle_ready",
))
# The elected #1513 authority uses the same bounded in-process manager.  The
# server must never admit more durable launches than a takeover client can
# restore and simulate.
PROJECTILE_MAX_ACTIVE = 128
PROJECTILE_MAX_PER_SHOOTER = 32
PROJECTILE_MAX_ID = 2147483647
PROJECTILE_MAX_PROGRESS_BATCH = 30
PROJECTILE_MAX_SPLASH_TARGETS = 30
PROJECTILE_MAX_DESTRUCTIBLES = 64
PROJECTILE_MAX_LIFETIME_MS = 20000
PROJECTILE_MAX_GRAVITY = 500.0
PROJECTILE_CLOCK_LEEWAY_MS = 250
PROJECTILE_TOLERANCE = 0.001
PROJECTILE_CAPABILITY = "projectile_ledger_v1"
AUTHORITY_DESCRIPTOR_TIMEOUT_SECONDS = 30.0
AUTHORITY_DESTRUCTIBLE_TIMEOUT_SECONDS = 120.0
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


def _bot_combat_log_message(event, players, bot_states):
    """Format one bot combat line with its real cause and both teams."""
    attacker_id = event.get("attacker", event.get("attacker_bot"))
    target_id = event.get("target", event.get("target_bot"))
    attacker = (players.get(attacker_id) if "attacker" in event else
                bot_states.get(attacker_id))
    target = (players.get(target_id) if "target" in event else
              bot_states.get(target_id))
    attacker_team = (attacker.team if hasattr(attacker, "team") else
                     attacker.get("team") if isinstance(attacker, dict) else
                     None)
    target_team = (target.team if hasattr(target, "team") else
                   target.get("team") if isinstance(target, dict) else None)
    return (
        "BOT COMBAT kind=%s source=%s attacker=%s attacker_team=%s "
        "target=%s target_team=%s damage=%s health=%s dead=%s" % (
            event.get("kind"), event.get("source"), attacker_id,
            attacker_team, target_id, target_team, event.get("damage"),
            event.get("health"), event.get("dead")))


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


def _exact_int(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    if low is not None and value < low:
        raise ValueError("integer below lower bound")
    if high is not None and value > high:
        raise ValueError("integer above upper bound")
    return value


def _bounded_float(value, low, high, inclusive_low=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected finite number")
    value = float(value)
    if (not math.isfinite(value) or value > high or
            (value < low if inclusive_low else value <= low)):
        raise ValueError("number outside bounds")
    return value


def _bounded_vector(value, lows, highs):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("expected three-vector")
    return [round(_bounded_float(component, lows[index], highs[index]), 6)
            for index, component in enumerate(value)]


def _message_fingerprint(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


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


def _critical_damage_transition(previous, current):
    """Return whether a critical payload contains new module/crew damage."""
    if not isinstance(current, dict):
        return False
    for event in current.get("events") or ():
        if not isinstance(event, dict) or event.get("cause") == "repair":
            continue
        kind = event.get("kind")
        state = event.get("state")
        if ((kind == "device" and state in ("critical", "destroyed")) or
                (kind == "crew" and state == "destroyed") or
                (kind == "fire" and bool(state)) or
                (kind == "ammo_rack" and state == "destroyed")):
            return True

    previous = previous if isinstance(previous, dict) else {}
    old_devices = {
        str(record.get("name")): float(record.get("hp", 0.0))
        for record in previous.get("devices") or ()
        if isinstance(record, dict) and record.get("name") is not None
    }
    for record in current.get("devices") or ():
        if not isinstance(record, dict) or record.get("name") is None:
            continue
        name = str(record.get("name"))
        hp = _finite_float(record.get("hp"))
        old_hp = old_devices.get(name)
        if old_hp is not None and hp < old_hp - 0.0001:
            return True
        if (old_hp is None and hp + 0.0001 <
                _finite_float(record.get("max_hp"), hp)):
            return True
    if (set(current.get("destroyed") or ()) -
            set(previous.get("destroyed") or ())):
        return True
    if (set(current.get("crew_ko") or ()) -
            set(previous.get("crew_ko") or ())):
        return True
    return ((bool(current.get("fire")) and
             not bool(previous.get("fire"))) or
            (bool(current.get("ammo_rack_death")) and
             not bool(previous.get("ammo_rack_death"))))


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
    capabilities: Tuple[str, ...] = field(default_factory=tuple)
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
    def __init__(self, map_name=DEFAULT_MAP, max_players=30, clock=None,
                 authority_mode="client"):
        self.map_option = map_name
        self.map_name = self._choose_map()
        self.authority_mode = (
            "client" if str(authority_mode) == "client" else "server")
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
        self.authority_epoch = 0
        self.bot_manifest_authority_id = None
        self.bot_manifest = []
        self.bot_states = {}
        self.bot_state_revision = 0
        self.bot_planner = BotPlanner()
        self.server_authority = None
        self.descriptor_store = DescriptorStore()
        self.vehicle_catalogs = {}
        self.pending_descriptor_names = ()
        self.descriptor_requested_names = ()
        self.descriptor_failed_names = set()
        self.authority_prerequisite_deadline = None
        self.authority_status = "idle"
        self.authority_fallback_reason = ""
        self._monotonic = clock or time.monotonic
        self.destructible_maps = {}
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
        self.capture_threat_bases = {1: [], 2: []}
        self.capture_contributors = {1: {}, 2: {}}
        self.capture_cursors = {1: 0, 2: 0}
        self.destructibles = {}
        self.destructible_revision = 0
        self.projectiles = {}
        self.projectile_tombstones = {}
        self.projectile_revision = 0
        self.bot_pending_projectile_launches = set()
        self.last_bot_state_reject = ""
        self.last_bot_state_reject_code = ""
        self.last_bot_hit_reject = ""
        self.last_bot_hit_reject_code = ""
        self.last_bot_human_hit_reject = ""
        self.last_bot_human_hit_reject_code = ""
        self._logged_protocol_reject_codes = {}

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

    def _server_time_ms(self):
        return max(0, int(round(float(self.tick) * 1000.0 / TICK_HZ)))

    def _projectile_snapshot(self):
        result = []
        for projectile_id in sorted(self.projectiles):
            record = self.projectiles[projectile_id]
            result.append({
                "projectile_id": record["projectile_id"],
                "shooter_kind": record["shooter_kind"],
                "shooter_id": record["shooter_id"],
                "source_vehicle": record["source_vehicle"],
                "shot_seq": record["shot_seq"],
                "shell_index": record["shell_index"],
                "team": record["team"],
                "origin": list(record["origin"]),
                "velocity": list(record["velocity"]),
                "gravity": record["gravity"],
                "max_distance": record["max_distance"],
                "max_time_ms": record["max_time_ms"],
                "is_he": record["is_he"],
                "splash_radius": record["splash_radius"],
                "penetration_factor": record["penetration_factor"],
                "launch_server_time_ms": record["launch_server_time_ms"],
                "checked_through_ms": record["checked_through_ms"],
                "checked_distance": record["checked_distance"],
                "piercing_loss": record["piercing_loss"],
                "authority_epoch": self.authority_epoch,
            })
        return result

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

    def _set_protocol_reject(self, kind, code, detail):
        """Record one exact rejection without changing protocol semantics."""
        setattr(self, "last_%s_reject_code" % kind, str(code))
        setattr(self, "last_%s_reject" % kind, str(detail))
        return False

    def _clear_protocol_reject(self, kind):
        setattr(self, "last_%s_reject_code" % kind, "")
        setattr(self, "last_%s_reject" % kind, "")

    def should_log_protocol_reject(self, kind, accepted):
        """Log only the first rejection in one continuous reason cascade."""
        if accepted:
            self._logged_protocol_reject_codes.pop(kind, None)
            return False
        code = getattr(self, "last_%s_reject_code" % kind, "unknown")
        if self._logged_protocol_reject_codes.get(kind) == code:
            return False
        self._logged_protocol_reject_codes[kind] = code
        return True

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
        if (self.server_authority is not None and
                self.client_build == CLIENT_BUILD_0922):
            connected = [SERVER_AUTHORITY_ID]
        else:
            connected = sorted(
                p.player_id for p in self.players.values()
                if p.connected and
                (self.phase not in ("loading", "battle") or p.participating))
        old = self.bot_authority_id
        self.bot_authority_id = connected[0] if connected else None
        if (old != self.bot_authority_id and
                self.client_build == CLIENT_BUILD_0922):
            self.authority_epoch += 1
            self.bot_pending_projectile_launches.clear()
        if (old != self.bot_authority_id and
                self.phase in ("loading", "battle")):
            self.bot_manifest_authority_id = None
            self.bot_planner.clear_observations()
            event = {
                "kind": "authority",
                "player_id": self.bot_authority_id,
                "round_id": self.round_id,
            }
            if self.client_build == CLIENT_BUILD_0922:
                event["authority_epoch"] = self.authority_epoch
            self.pending_events.append(event)
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
            capabilities = ()
            if client_build == CLIENT_BUILD_0922:
                raw_capabilities = hello.get("capabilities", ())
                if (not isinstance(raw_capabilities, list) or
                        len(raw_capabilities) > 32 or
                        any(not isinstance(value, str) or not value or
                            len(value) > 64
                            for value in raw_capabilities) or
                        len(set(raw_capabilities)) != len(raw_capabilities)):
                    return None, "unsupported_capabilities"
                capabilities = tuple(raw_capabilities)
                if PROJECTILE_CAPABILITY not in capabilities:
                    return None, "unsupported_capabilities"
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
                capabilities=capabilities,
            )
            self.players[player_id] = player
            if self.host_player_id is None:
                self.host_player_id = player_id
            self.state_revision += 1
            return player, None

    def select_vehicle(self, player_id, message):
        """Apply one waiting-room garage change before the next round."""
        with self.lock:
            player = self.players.get(player_id)
            if (player is None or not player.connected or
                    self.phase != "waiting"):
                return False
            vehicle = _safe_vehicle(message.get("vehicle"), player.vehicle)
            max_health = max(1, min(int(_finite_float(
                message.get("max_health"), player.max_health)), 100000))
            if vehicle == player.vehicle and max_health == player.max_health:
                return False
            player.vehicle = vehicle
            player.max_health = max_health
            player.health = max_health
            self.state_revision += 1
            return True

    def remove_player(self, player_id):
        with self.lock:
            player = self.players.pop(player_id, None)
            if player is not None:
                player.connected = False
                self.state_revision += 1
            self.vehicle_catalogs.pop(player_id, None)
            if (self.server_authority is not None and
                    self.authority_status == "server_pending" and
                    player_id == self.host_player_id):
                # The sole native-data donor left while either projection or
                # destructible identities were pending. End the round rather
                # than wait for an impossible retry.
                reason = ("descriptor_donor_disconnected"
                          if not self.server_authority.started()
                          else "destructible_map_donor_disconnected")
                self._fail_server_authority_round(reason)
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
        self.authority_epoch = 0
        self.bot_manifest_authority_id = None
        self.bot_manifest = []
        self.bot_states = {}
        self.bot_state_revision = 0
        self.bot_planner.reset()
        self.server_authority = None
        self.pending_descriptor_names = ()
        self.descriptor_requested_names = ()
        self.descriptor_failed_names = set()
        self.authority_prerequisite_deadline = None
        self.authority_status = "idle"
        self.authority_fallback_reason = ""
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
        self.capture_threat_bases = {1: [], 2: []}
        self.capture_contributors = {1: {}, 2: {}}
        self.capture_cursors = {1: 0, 2: 0}
        self.destructibles = {}
        self.destructible_revision = 0
        self.projectiles = {}
        self.projectile_tombstones = {}
        self.projectile_revision = 0
        self.bot_pending_projectile_launches = set()
        self.last_bot_state_reject = ""
        self.last_bot_state_reject_code = ""
        self.last_bot_hit_reject = ""
        self.last_bot_hit_reject_code = ""
        self.last_bot_human_hit_reject = ""
        self.last_bot_human_hit_reject_code = ""
        self._logged_protocol_reject_codes = {}
        self._elect_room_host()
        self.state_revision += 1

    def _authority_fields(self):
        return {
            "authority_status": self.authority_status,
            "authority_fallback_reason": self.authority_fallback_reason,
        }

    def _wait_for_authority_prerequisite(
            self, timeout=AUTHORITY_DESCRIPTOR_TIMEOUT_SECONDS,
            restart=False):
        if restart or self.authority_prerequisite_deadline is None:
            self.authority_prerequisite_deadline = (
                float(self._monotonic()) + float(timeout))
        self.authority_status = "server_pending"
        self.authority_fallback_reason = ""

    def _server_authority_prerequisites_ready(self):
        authority = self.server_authority
        return bool(
            authority is not None and authority.started() and
            authority.world.destructible_identities_ready())

    def _mark_server_authority_ready(self):
        if not self._server_authority_prerequisites_ready():
            return False
        self.authority_status = "server"
        self.authority_fallback_reason = ""
        self.authority_prerequisite_deadline = None
        return True

    def _fail_server_authority_round(self, reason):
        """Abort a #1513 round whose server-authority prerequisites failed.

        The server owns every #1513 battle simulation, so a failed
        prerequisite ends the round instead of degrading to a
        client-simulated battle.
        """
        reason = str(reason)
        authority = self.server_authority
        if (authority is not None and
                not authority.world.destructible_identities_ready()):
            self.destructible_maps.pop(self.map_name, None)
        _server_log("SERVER AUTHORITY hard fail reason=%s round=%s" %
                    (reason, self.round_id))
        self._reset_round()
        self.authority_status = "failed"
        self.authority_fallback_reason = reason
        return True

    def _refuse_battle_start(self, reason):
        """Refuse a #1513 start that cannot run under the server authority."""
        reason = str(reason)
        self.phase = "waiting"
        self.roster_finalized = False
        self.server_authority = None
        self.pending_descriptor_names = ()
        self.descriptor_requested_names = ()
        self.descriptor_failed_names = set()
        self.authority_prerequisite_deadline = None
        self.authority_status = "failed"
        self.authority_fallback_reason = reason
        self.state_revision += 1
        _server_log("SERVER AUTHORITY refused start reason=%s" % reason)
        return None, reason

    def _expire_authority_prerequisite(self):
        if (self.phase != "loading" or
                self.authority_status != "server_pending" or
                self.authority_prerequisite_deadline is None or
                float(self._monotonic()) <
                float(self.authority_prerequisite_deadline)):
            return False
        authority = self.server_authority
        reason = ("descriptor_timeout"
                  if authority is None or not authority.started()
                  else "destructible_map_timeout")
        return self._fail_server_authority_round(reason)

    def lobby_message(self):
        with self.lock:
            message = {
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
            if self.client_build == CLIENT_BUILD_0922:
                message["authority_epoch"] = self.authority_epoch
                message.update(self._authority_fields())
            return message

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
            if (self.client_build == CLIENT_BUILD_0922 and any(
                    PROJECTILE_CAPABILITY not in participant.capabilities
                    for participant in self.players.values()
                    if participant.connected)):
                return None, "missing_projectile_capability"
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
            refusal = self._prepare_server_authority()
            if refusal is not None:
                return self._refuse_battle_start(refusal)
            self._elect_bot_authority()
            descriptor_request = None
            if self.server_authority is not None:
                try:
                    prepared = self.server_authority.prepare_lineup(
                        self.vehicle_catalogs.get(self.host_player_id),
                        list(self.bot_roster),
                        [self._public_player(p) for p in connected],
                        player.vehicle)
                except Exception as error:
                    _server_log(
                        "server authority lineup preparation failed: %s" %
                        error)
                    prepared = False
                if not prepared:
                    return self._refuse_battle_start("lineup_unavailable")
                names = self.server_authority.required_projections()
                missing = tuple(sorted(
                    name for name in names
                    if self.descriptor_store.get(name) is None))
                self.pending_descriptor_names = missing
                self.descriptor_requested_names = missing
                self.descriptor_failed_names = set()
                if missing:
                    self._wait_for_authority_prerequisite(restart=True)
                    descriptor_request = {
                        "type": "descriptor_request",
                        "round_id": self.round_id,
                        "names": list(missing),
                    }
                elif not self._start_server_authority():
                    return self._refuse_battle_start("server_start_failed")
                elif not self._mark_server_authority_ready():
                    self._wait_for_authority_prerequisite(
                        AUTHORITY_DESTRUCTIBLE_TIMEOUT_SECONDS,
                        restart=True)
            self.state_revision += 1
            start_message = {
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
            }
            if self.client_build == CLIENT_BUILD_0922:
                start_message.update({
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": self._server_time_ms(),
                })
            if descriptor_request is not None:
                host = self.players.get(self.host_player_id)
                if host is None or not host.send(descriptor_request):
                    return self._refuse_battle_start(
                        "descriptor_request_failed")
            start_message["bot_authority_id"] = self.bot_authority_id
            if self.client_build == CLIENT_BUILD_0922:
                start_message["authority_epoch"] = self.authority_epoch
                start_message.update(self._authority_fields())
            if (self.server_authority is not None and
                    not self.server_authority.world.destructible_identities_ready()):
                start_message["need_destructible_map"] = True
            return start_message, None

    def _prepare_server_authority(self):
        """Build this round's server-hosted authority when it can own bots.

        Returns None on success and a refusal reason when the #1513 round
        cannot run under the server authority.
        """
        self.server_authority = None
        self.pending_descriptor_names = ()
        self.descriptor_requested_names = ()
        self.descriptor_failed_names = set()
        self.authority_prerequisite_deadline = None
        self.authority_status = "idle"
        self.authority_fallback_reason = ""
        if self.client_build != CLIENT_BUILD_0922:
            return None
        if self.authority_mode == "client":
            _server_log(
                "AUTHORITY MODE client: this round elects a client authority")
            return None
        if not self.vehicle_catalogs.get(self.host_player_id):
            return "vehicle_catalog_unavailable"
        try:
            world = server_world.load_world(self.map_name)
        except Exception as error:
            _server_log("server authority world load failed: %s" % error)
            return "world_data_unavailable"
        if world is None:
            _server_log(
                "server authority has no baked world for %s" % self.map_name)
            return "world_data_unavailable"
        self.server_authority = ServerBattleAuthority(
            self, world, self.descriptor_store)
        self._wait_for_authority_prerequisite(restart=True)
        return None

    def store_vehicle_catalog(self, player_id, message):
        """Keep one connection's eligible-vehicle catalog for lineups."""
        with self.lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return False
            rows = message.get("vehicles")
            if not isinstance(rows, list) or not rows or len(rows) > 1024:
                _server_log(
                    "DESCRIPTOR CATALOG rejected id=%d rows=%s" % (
                        player_id, len(rows) if isinstance(rows, list)
                        else type(rows).__name__))
                return False
            catalog = []
            seen = set()
            for index, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    _server_log(
                        "DESCRIPTOR CATALOG rejected id=%d row=%d: "
                        "not an object" % (player_id, index))
                    return False
                name = _safe_vehicle(raw.get("name"), "")
                try:
                    level = int(raw.get("level"))
                except (TypeError, ValueError):
                    level = 0
                tags = raw.get("tags")
                if (not name or name in seen or not 1 <= level <= 10 or
                        not isinstance(tags, list) or len(tags) > 32):
                    _server_log(
                        "DESCRIPTOR CATALOG rejected id=%d row=%d: %r" % (
                            player_id, index, raw.get("name")))
                    return False
                seen.add(name)
                catalog.append({
                    "name": name, "level": level,
                    "tags": tuple(sorted(str(tag)[:32] for tag in tags)),
                })
            self.vehicle_catalogs[player_id] = tuple(catalog)
            _server_log("DESCRIPTOR CATALOG stored id=%d rows=%d" % (
                player_id, len(catalog)))
            return True

    def store_destructible_map(self, player_id, message):
        """Cache one map's donated native identities and install them."""
        with self.lock:
            player = self.players.get(player_id)
            if (player is None or not player.connected or
                    player_id != self.host_player_id or
                    not self._message_round_matches(message)):
                return False
            map_name = str(message.get("map") or "")
            if not map_name:
                return False
            try:
                part = int(message.get("part"))
                parts = int(message.get("parts"))
                unit_mass = float(message.get("unit_vehicle_mass"))
            except (TypeError, ValueError):
                return False
            if not 0 <= part < parts or parts > 64 or unit_mass <= 0.0:
                return False
            rows = message.get("instances")
            resources = message.get("resources")
            if (not isinstance(rows, list) or len(rows) > 2000 or
                    not isinstance(resources, dict)):
                return False
            for row in rows:
                if (not isinstance(row, list) or len(row) != 5 or
                        not isinstance(row[0], list) or
                        len(row[0]) != 12):
                    return False
            cache = self.destructible_maps.setdefault(map_name, {
                "unit_vehicle_mass": unit_mass,
                "resources": {},
                "instances": {},
                "parts_seen": set(),
                "parts": parts,
            })
            if cache["parts"] != parts:
                return False
            for name, raw in resources.items():
                if isinstance(raw, dict):
                    cache["resources"][str(name)] = {
                        "destr_type": str(raw.get("destr_type") or ""),
                        "kinetic_correction": _finite_float(
                            raw.get("kinetic_correction"), 0.0),
                    }
            for row in rows:
                cache["instances"][tuple(int(v) for v in row[0])] = row
            cache["parts_seen"].add(part)
            if cache["parts_seen"] != set(range(parts)):
                return True
            self._install_destructible_map(map_name)
            if (map_name == self.map_name and
                    self._mark_server_authority_ready()):
                self.state_revision += 1
                return "ready"
            if (map_name == self.map_name and
                    self.server_authority is not None and
                    not self.server_authority.world.destructible_identities_ready()):
                self._fail_server_authority_round(
                    "destructible_map_incomplete")
                return "failed"
            return True

    def _install_destructible_map(self, map_name):
        if (self.server_authority is None or
                map_name != self.map_name):
            return 0
        cache = self.destructible_maps.get(map_name)
        if cache is None or cache["parts_seen"] != set(
                range(cache["parts"])):
            return 0
        installed = self.server_authority.world.install_destructible_map(
            list(cache["instances"].values()), cache["resources"],
            cache["unit_vehicle_mass"])
        if installed:
            _server_log("DESTRUCTIBLE MAP installed map=%s instances=%d" % (
                map_name, installed))
        return installed

    def donate_descriptors(self, player_id, message):
        """Admit requested projections; start the authority when complete."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "loading" or
                    self.server_authority is None or
                    self.server_authority.started() or
                    player_id != self.host_player_id):
                return False
            projections = message.get("projections")
            requested = message.get("requested")
            failures = message.get("failures")
            complete = message.get("complete")
            if (not isinstance(projections, dict) or len(projections) > 64 or
                    not isinstance(requested, list) or not requested or
                    len(requested) > 64 or
                    not isinstance(failures, list) or len(failures) > 64 or
                    not isinstance(complete, bool)):
                return False
            clean_requested = []
            for raw in requested:
                if not isinstance(raw, str):
                    return False
                clean = _safe_vehicle(raw, "")
                if not clean or clean != raw or clean in clean_requested:
                    return False
                clean_requested.append(clean)
            if tuple(clean_requested) != self.descriptor_requested_names:
                return False
            wanted = set(clean_requested)
            clean_failures = set()
            for raw in failures:
                if not isinstance(raw, str):
                    return False
                clean = _safe_vehicle(raw, "")
                if (not clean or clean != raw or clean not in wanted or
                        clean in clean_failures):
                    return False
                clean_failures.add(clean)
            for name, raw in projections.items():
                clean = _safe_vehicle(name, "")
                if (not isinstance(name, str) or not clean or clean != name or
                        clean not in wanted or clean in clean_failures):
                    return False
                if not isinstance(raw, dict):
                    return False
                try:
                    self.descriptor_store.add(clean, raw)
                except ValueError:
                    return False
            self.descriptor_failed_names.update(clean_failures)
            missing = [name for name in self.descriptor_requested_names
                       if self.descriptor_store.get(name) is None]
            self.pending_descriptor_names = tuple(missing)
            if not complete:
                return True
            if self.descriptor_failed_names or missing:
                self._fail_server_authority_round(
                    "descriptor_projection_failed")
                return "failed"
            self.pending_descriptor_names = ()
            self.descriptor_requested_names = ()
            self.descriptor_failed_names = set()
            if not self._start_server_authority():
                self._fail_server_authority_round("server_start_failed")
                return "failed"
            if not self._mark_server_authority_ready():
                self._wait_for_authority_prerequisite(
                    AUTHORITY_DESTRUCTIBLE_TIMEOUT_SECONDS, restart=True)
            return "started"

    def _start_server_authority(self):
        authority = self.server_authority
        message = {
            "round_id": self.round_id,
            "map": self.map_name,
            "bots": list(self.bot_roster),
            "bot_manifest": [],
            "bot_authority_id": SERVER_AUTHORITY_ID,
            "bot_order_revision": self.bot_orders["revision"],
            "bot_orders": list(self.bot_orders["orders"]),
            "battle_result": self.battle_result,
        }
        try:
            authority.battle_start(message, float(self.tick) / TICK_HZ)
        except Exception as error:
            _server_log("server authority start failed: %s" % error)
            return False
        bases = self._sanitize_capture_bases(authority.capture_bases())
        if bases:
            self.capture_bases = bases
        self._install_destructible_map(self.map_name)
        return True

    def _activate_battle_if_ready(self):
        if self.phase != "loading":
            return None
        if (self.client_build == CLIENT_BUILD_0922 and
                self.authority_mode != "client" and
                self.server_authority is None):
            return None
        if (self.server_authority is not None and
                not self._server_authority_prerequisites_ready()):
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
            "bot_authority_id": self.bot_authority_id,
            "authority_epoch": self.authority_epoch,
            "server_time_ms": self._server_time_ms(),
            "countdown_seconds": PREBATTLE_SECONDS,
            "battle_duration_seconds": BATTLE_DURATION_SECONDS,
            "timing": self._timing_payload(),
        }
        live_message.update(self._authority_fields())
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
            message = {
                "type": "snapshot",
                "protocol": PROTOCOL_VERSION,
                "client_build": self.client_build,
                "server_tick": 0,
                "round_id": self.round_id,
                "map": self.map_name,
                "bot_authority_id": self.bot_authority_id,
                "authority_epoch": self.authority_epoch,
                "server_time_ms": self._server_time_ms(),
                "players": [self._public_player(p) for p in self.players.values()
                            if p.connected and p.participating],
                "bots": [self.bot_states[key] for key in sorted(self.bot_states)],
                "bot_state_revision": self.bot_state_revision,
                "projectile_revision": self.projectile_revision,
                "projectiles": self._projectile_snapshot(),
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "destructibles": list(self.destructibles.values()),
            }
            message.update(self._authority_fields())
            return message

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
        """Admit one resolved map destruction into shared LAN state."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None):
                return False
            if player_id != self.bot_authority_id or player_id != \
                    SERVER_AUTHORITY_ID:
                player = self.players.get(player_id)
                if (player is None or not player.connected or
                        not player.participating or not player.alive):
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
            self._mark_world_destroyed(event)
            return True

    def _mark_world_destroyed(self, event):
        if self.server_authority is None:
            return
        self.server_authority.world.mark_destroyed_wire(
            event["chunk_id"], event["item_index"],
            event.get("mat_kind") if
            event["destructible_kind"] == "module" else None)

    @staticmethod
    def _destructible_key(event):
        return (event["destructible_kind"], event["chunk_id"],
                event["item_index"], event.get("mat_kind"))

    def _normalize_projectile_destructibles(self, receipts):
        """Validate one bounded embedded shot-destruction transaction."""
        if (not isinstance(receipts, list) or
                len(receipts) > PROJECTILE_MAX_DESTRUCTIBLES):
            raise ValueError("invalid projectile destructible batch")
        normalized = []
        seen = set()
        for raw in receipts:
            allowed = {
                "destructible_kind", "chunk_id", "item_index", "x", "y",
                "z", "fall_yaw", "speed", "is_shot", "mat_kind",
            }
            required = allowed - {"mat_kind"}
            if (not isinstance(raw, dict) or set(raw) - allowed or
                    not required.issubset(raw)):
                raise ValueError("invalid projectile destructible shape")
            event = self._sanitize_destructible(raw)
            if event is None or event.get("is_shot") is not True:
                raise ValueError("invalid projectile destructible receipt")
            key = self._destructible_key(event)
            if key in seen:
                raise ValueError("duplicate projectile destructible receipt")
            seen.add(key)
            normalized.append(event)
        return normalized

    def _commit_projectile_destructibles(self, player_id, receipts):
        """Commit only prevalidated receipts; this helper cannot reject."""
        changed = 0
        for event in receipts:
            key = self._destructible_key(event)
            if key in self.destructibles:
                continue
            self.destructible_revision += 1
            stored = dict(event)
            stored["revision"] = self.destructible_revision
            stored["reported_by"] = int(player_id)
            self.destructibles[key] = stored
            self.pending_events.append(dict(stored))
            self._mark_world_destroyed(stored)
            changed += 1
        return changed

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
            message = {
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
            if self.client_build == CLIENT_BUILD_0922:
                message.update({
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": self._server_time_ms(),
                    "projectile_revision": self.projectile_revision,
                    "projectiles": self._projectile_snapshot(),
                })
                message.update(self._authority_fields())
            return message

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

    @staticmethod
    def _sanitize_bot_ammo(raw, identity, previous):
        """Validate an optional atomic finite-ammunition snapshot."""
        has_inventory = "ammo_remaining" in raw
        has_next = "next_shell_index" in raw
        has_pending = "ammo_reload_pending" in raw
        if not has_inventory and not has_next and not has_pending:
            return {
                "remaining": list((previous or {}).get(
                    "ammo_remaining", [])),
                "next": int((previous or {}).get(
                    "next_shell_index", raw.get("shell_index", 0))),
                "pending": bool((previous or {}).get(
                    "ammo_reload_pending", False)),
            }
        if (not has_inventory or not has_next or not has_pending or
                "shell_index" not in raw):
            raise ValueError("bot ammunition snapshot is incomplete")
        shells = ((identity.get("profile") or {}).get("shells") or [])
        remaining = raw.get("ammo_remaining")
        if not isinstance(remaining, (list, tuple)):
            raise ValueError("bot ammunition inventory shape is invalid")
        # Production manifests carry descriptor shell summaries. Engine-free
        # harnesses and legacy adapters may omit them; the atomic inventory is
        # still self-sizing and bounded, so preserve it without inventing a
        # shell catalogue on the server.
        shell_count = len(shells) if shells else len(remaining)
        if (shell_count <= 0 or shell_count > 5 or
                len(remaining) != shell_count):
            raise ValueError("bot ammunition inventory shape is invalid")
        parsed = [_exact_int(value, 0, 1000) for value in remaining]
        if sum(parsed) > 1000:
            raise ValueError("bot ammunition inventory exceeds capacity")
        loaded = _exact_int(raw.get("shell_index"), 0, shell_count - 1)
        planned = _exact_int(
            raw.get("next_shell_index"), 0, shell_count - 1)
        pending = raw.get("ammo_reload_pending")
        if not isinstance(pending, bool):
            raise ValueError("bot ammunition reload state is invalid")
        total = sum(parsed)
        if total > 0 and parsed[planned] <= 0:
            raise ValueError("bot planned ammunition is exhausted")
        if total > 0 and not pending and parsed[loaded] <= 0:
            raise ValueError("bot loaded ammunition is exhausted")
        return {"remaining": parsed, "next": planned, "loaded": loaded,
                "pending": pending}

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
            accepted_visibility = []
            accepted_contacts = self.bot_planner.report_contacts(
                message.get("contacts"), known_targets, now,
                accepted_visibility=accepted_visibility)
            known_bots = self.bot_planner.known_bots(
                self.bot_manifest, list(self.bot_states.values()))
            accepted_affordances = self.bot_planner.report_affordances(
                message.get("affordances"), known_bots, known_targets, now)
            if (self.client_build == CLIENT_BUILD_0922 and
                    accepted_visibility):
                return {
                    "type": "bot_observation",
                    "protocol": PROTOCOL_VERSION,
                    "round_id": self.round_id,
                    "contacts": accepted_visibility,
                }
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
        ammunition = BattleState._sanitize_bot_ammo(
            raw, identity, previous)
        result = {
            "id": int(identity["id"]),
            "team": int(identity["team"]),
            "slot": int(identity["slot"]),
            "name": identity["name"],
            "vehicle": identity.get("vehicle", "ussr:R11_MS-1"),
            "world_pose": bool(raw.get(
                "world_pose", (previous or {}).get("world_pose", True))),
            "x": round(_clamp(_finite_float(raw.get("x")), -2000.0, 2000.0), 4),
            "y": round(_clamp(_finite_float(raw.get("y")), -1000.0, 1000.0), 4),
            "z": round(_clamp(_finite_float(raw.get("z")), -2000.0, 2000.0), 4),
            "yaw": round(yaw, 5),
            "pitch": round(_clamp(_finite_float(raw.get("pitch")), -0.61, 0.61), 5),
            "roll": round(_clamp(_finite_float(raw.get("roll")), -0.61, 0.61), 5),
            "aim_yaw": round(_finite_float(raw.get("aim_yaw"), yaw), 5),
            "gun_pitch": round(_clamp(_finite_float(raw.get("gun_pitch")), -1.2, 1.2), 5),
            "movement_dir": (1 if movement > 0.01 else
                             (-1 if movement < -0.01 else 0)),
            "rotation_dir": (1 if rotation > 0.01 else
                             (-1 if rotation < -0.01 else 0)),
            "fire_seq": fire_seq,
            "shell_index": ammunition.get(
                "loaded", max(0, min(int(_finite_float(
                    raw.get("shell_index"), 0)), 9))),
            "next_shell_index": ammunition["next"],
            "ammo_remaining": ammunition["remaining"],
            "ammo_reload_pending": ammunition["pending"],
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
    def _validate_bot_ammo_transition(previous, current):
        """Require conserved inventory and explicit reload-boundary state."""
        if previous is None:
            return True
        before = previous.get("ammo_remaining") or []
        after = current.get("ammo_remaining") or []
        if not before and not after:
            return True
        if len(before) != len(after) or not before:
            raise ValueError("bot ammunition contract appeared mid-round")
        fire_delta = (int(current.get("fire_seq", 0)) -
                      int(previous.get("fire_seq", 0)))
        if fire_delta not in (0, 1):
            raise ValueError("bot ammunition fire delta is invalid")
        loaded = int(current.get("shell_index", 0))
        previous_loaded = int(previous.get("shell_index", 0))
        previous_next = int(previous.get(
            "next_shell_index", previous_loaded))
        next_shell = int(current.get("next_shell_index", loaded))
        previous_pending = bool(previous.get(
            "ammo_reload_pending", False))
        pending = bool(current.get("ammo_reload_pending", False))
        expected = list(before)
        if fire_delta:
            if not pending:
                raise ValueError("bot shot did not enter reload state")
            expected_loaded = (previous_next if previous_pending else
                               previous_loaded)
            if loaded != expected_loaded:
                raise ValueError("bot loaded shell changed while firing")
            if loaded >= len(expected) or expected[loaded] <= 0:
                raise ValueError("bot fired an exhausted shell")
            expected[loaded] -= 1
        elif not previous_pending:
            if pending:
                raise ValueError("bot reload started without a shot")
            if loaded != previous_loaded:
                raise ValueError("bot loaded shell changed outside reload")
        elif pending:
            if loaded != previous_loaded:
                raise ValueError("bot loaded shell changed before reload")
            if next_shell != previous_next:
                raise ValueError("bot planned shell changed before reload")
        elif loaded != previous_next:
            raise ValueError("bot loaded shell skipped its planned boundary")
        if list(after) != expected:
            raise ValueError("bot ammunition inventory is not conserved")
        return True

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
            self._clear_protocol_reject("bot_state")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    "bot_state", "round",
                    "round=%s server_round=%s" % (
                        message.get("round_id") if isinstance(message, dict)
                        else None, self.round_id))
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    "bot_state", "combat_closed",
                    "phase=%s tick=%s" % (self.phase, self.tick))
            if self.battle_result is not None:
                return self._set_protocol_reject(
                    "bot_state", "battle_finished", "battle_result=set")
            if player_id != self.bot_authority_id:
                return self._set_protocol_reject(
                    "bot_state", "authority",
                    "sender=%s authority=%s" % (
                        player_id, self.bot_authority_id))
            if player_id != self.bot_manifest_authority_id:
                return self._set_protocol_reject(
                    "bot_state", "manifest_authority",
                    "sender=%s manifest_authority=%s" % (
                        player_id, self.bot_manifest_authority_id))
            if not self.bot_manifest:
                return self._set_protocol_reject(
                    "bot_state", "manifest_missing", "manifest=empty")
            identities = {entry["id"]: entry for entry in self.bot_manifest}
            incoming = message.get("bots") or []
            if (not isinstance(incoming, (list, tuple)) or
                    len(incoming) != len(identities)):
                return self._set_protocol_reject(
                    "bot_state", "batch_shape",
                    "incoming_type=%s incoming_count=%s expected_count=%s" % (
                        type(incoming).__name__,
                        len(incoming) if isinstance(
                            incoming, (list, tuple)) else None,
                        len(identities)))
            next_states = {}
            shot_events = []
            pending_projectile_launches = set()
            fire_deaths = []
            capture_resets = set()
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
                    return self._set_protocol_reject(
                        "bot_state", "bot_shape",
                        "bot=%s required_or_finite_fields_invalid" % (
                            raw.get("id") if isinstance(raw, dict)
                            else None))
                try:
                    bot_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    return self._set_protocol_reject(
                        "bot_state", "bot_id", "bot=%s" % raw.get("id"))
                identity = identities.get(bot_id)
                if identity is None or bot_id in seen:
                    return self._set_protocol_reject(
                        "bot_state", "bot_identity",
                        "bot=%s known=%s duplicate=%s" % (
                            bot_id, identity is not None, bot_id in seen))
                seen.add(bot_id)
                try:
                    fire_seq = int(raw.get("fire_seq"))
                except (TypeError, ValueError):
                    return self._set_protocol_reject(
                        "bot_state", "fire_seq",
                        "bot=%s client_fire=%s" % (
                            bot_id, raw.get("fire_seq")))
                if (fire_seq < 0 or float(raw.get("fire_seq")) != fire_seq or
                        bool(raw.get("alive")) !=
                        (int(float(raw.get("health"))) > 0)):
                    return self._set_protocol_reject(
                        "bot_state", "fire_or_alive",
                        "bot=%s client_fire=%s health=%s alive=%s" % (
                            bot_id, raw.get("fire_seq"), raw.get("health"),
                            raw.get("alive")))
                previous = self.bot_states.get(bot_id)
                try:
                    current = self._sanitize_bot_state(
                        raw, identity, previous)
                    if self.client_build == CLIENT_BUILD_0922:
                        self._reconcile_modern_bot_combat(
                            raw, previous, current)
                except ValueError as error:
                    return self._set_protocol_reject(
                        "bot_state", "combat_contract",
                        ("bot=%s client_fire=%s server_fire=%s "
                         "client_base=%s server_base=%s client_seq=%s "
                         "server_ack=%s reason=%s") % (
                            bot_id, raw.get("fire_seq"),
                            (previous or {}).get("fire_seq", 0),
                            raw.get("combat_base_revision"),
                            (previous or {}).get(
                                "combat_base_revision", 0),
                            raw.get("combat_seq"),
                            (previous or {}).get("combat_ack_seq", 0),
                            error))
                previous_fire = int((previous or {}).get("fire_seq", 0))
                if current["fire_seq"] > previous_fire + 1:
                    return self._set_protocol_reject(
                        "bot_state", "fire_gap",
                        "bot=%s client_fire=%s server_fire=%s" % (
                            bot_id, current["fire_seq"], previous_fire))
                try:
                    self._validate_bot_ammo_transition(previous, current)
                except ValueError as error:
                    return self._set_protocol_reject(
                        "bot_state", "ammo_contract",
                        "bot=%s reason=%s" % (bot_id, error))
                next_states[bot_id] = current
                previous_fire_active = bool(
                    (previous or {}).get("critical") and
                    previous["critical"].get("fire", False))
                fire_tick_damage = max(1, int(
                    int(current.get("max_health", 0)) * 0.05))
                previous_health = int((previous or {}).get("health", 0))
                current_health = int(current.get("health", 0))
                if (previous is not None and
                        (current_health < previous_health or
                         _critical_damage_transition(
                             previous.get("critical"),
                             current.get("critical")))):
                    capture_resets.add(bot_id)
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
                    if self.client_build == CLIENT_BUILD_0922:
                        pending_projectile_launches.add(
                            (bot_id, current["fire_seq"]))
                    else:
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
                return self._set_protocol_reject(
                    "bot_state", "batch_members",
                    "missing=%s" % sorted(set(identities) - seen))
            self.bot_states = next_states
            self.bot_pending_projectile_launches.update(
                pending_projectile_launches)
            for bot_id in capture_resets:
                self._drop_capture_for_vehicle("bot", bot_id)
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
            self.bot_state_revision += 1
            return True

    @staticmethod
    def _projectile_message_fits(message):
        try:
            payload = json.dumps(
                message, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True)
        except (TypeError, ValueError, OverflowError):
            return False
        return len(payload.encode("utf-8")) + 1 <= MAX_LINE_BYTES

    @staticmethod
    def _projectile_id(round_id, shooter_kind, shooter_id, shot_seq):
        prefix = "p" if shooter_kind == "player" else "b"
        return "%d:%s:%d:%d" % (
            int(round_id), prefix, int(shooter_id), int(shot_seq))

    def _projectile_authority_matches(self, player_id, message):
        try:
            epoch = _exact_int(
                message.get("authority_epoch"), 0, PROJECTILE_MAX_ID)
        except ValueError:
            return False
        return (player_id == self.bot_authority_id and
                epoch == self.authority_epoch)

    def launch_projectile(self, player_id, message):
        """Atomically admit one #1513 shot into the round projectile ledger."""
        with self.lock:
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    not self._projectile_message_fits(message)):
                return False
            allowed = {
                "type", "round_id", "shooter_kind", "shooter_id",
                "shot_seq", "shell_index", "origin", "velocity",
                "gravity", "max_distance", "max_time_ms", "is_he",
                "splash_radius", "penetration_factor", "authority_epoch",
            }
            if set(message) - allowed:
                return False
            try:
                shooter_kind = str(message.get("shooter_kind"))
                if shooter_kind not in ("player", "bot"):
                    raise ValueError("invalid shooter kind")
                shooter_id = _exact_int(
                    message.get("shooter_id"), 1, PROJECTILE_MAX_ID)
                shot_seq = _exact_int(
                    message.get("shot_seq"), 1, PROJECTILE_MAX_ID)
                shell_index = _exact_int(message.get("shell_index"), 0, 9)
                origin = _bounded_vector(
                    message.get("origin"), (-5000.0, -1000.0, -5000.0),
                    (5000.0, 3000.0, 5000.0))
                velocity = _bounded_vector(
                    message.get("velocity"), (-3000.0, -3000.0, -3000.0),
                    (3000.0, 3000.0, 3000.0))
                speed = math.sqrt(sum(component * component
                                      for component in velocity))
                if speed <= 0.001 or speed > 3000.0:
                    raise ValueError("invalid launch speed")
                gravity = round(_bounded_float(
                    message.get("gravity"), 0.0,
                    PROJECTILE_MAX_GRAVITY, False), 6)
                max_distance = round(_bounded_float(
                    message.get("max_distance"), 0.0, 10000.0, False), 6)
                max_time_ms = _exact_int(
                    message.get("max_time_ms"), 1,
                    PROJECTILE_MAX_LIFETIME_MS)
                if not isinstance(message.get("is_he"), bool):
                    raise ValueError("invalid HE flag")
                is_he = message["is_he"]
                splash_radius = round(_bounded_float(
                    message.get("splash_radius"), 0.0, 100.0), 6)
                penetration_factor = round(_bounded_float(
                    message.get("penetration_factor"), 0.0, 100.0), 6)
                if not is_he and splash_radius != 0.0:
                    raise ValueError("AP projectile cannot have splash")
            except (TypeError, ValueError, OverflowError):
                return False

            projectile_id = self._projectile_id(
                self.round_id, shooter_kind, shooter_id, shot_seq)
            normalized = {
                "shooter_kind": shooter_kind, "shooter_id": shooter_id,
                "shot_seq": shot_seq, "shell_index": shell_index,
                "origin": origin, "velocity": velocity,
                "gravity": gravity, "max_distance": max_distance,
                "max_time_ms": max_time_ms, "is_he": is_he,
                "splash_radius": splash_radius,
                "penetration_factor": penetration_factor,
            }
            launch_fingerprint = _message_fingerprint(normalized)
            if shooter_kind == "player":
                shooter = self.players.get(shooter_id)
                if (shooter_id != player_id or shooter is None or
                        not shooter.connected):
                    return False
            else:
                if not self._projectile_authority_matches(player_id, message):
                    return False
                shooter = self.bot_states.get(shooter_id)
            active = self.projectiles.get(projectile_id)
            if active is not None:
                return active["launch_fingerprint"] == launch_fingerprint
            terminal = self.projectile_tombstones.get(projectile_id)
            if terminal is not None:
                return terminal["launch_fingerprint"] == launch_fingerprint

            if len(self.projectiles) >= PROJECTILE_MAX_ACTIVE:
                return False
            shooter_active = sum(
                1 for record in self.projectiles.values()
                if (record["shooter_kind"] == shooter_kind and
                    record["shooter_id"] == shooter_id))
            if shooter_active >= PROJECTILE_MAX_PER_SHOOTER:
                return False

            if shooter_kind == "player":
                if (not shooter.participating or
                        not shooter.alive or shot_seq != shooter.fire_seq + 1):
                    return False
                team = shooter.team
                source_vehicle = shooter.vehicle
            else:
                launch_edge = (shooter_id, shot_seq)
                if (shooter is None or not shooter.get("alive") or
                        shell_index != int(shooter.get("shell_index", 0)) or
                        launch_edge not in
                        self.bot_pending_projectile_launches or
                        shot_seq != int(shooter.get("fire_seq", 0))):
                    return False
                team = int(shooter.get("team", 0))
                if team not in (1, 2):
                    return False
                source_vehicle = str(shooter.get("vehicle", ""))
            if not source_vehicle or len(source_vehicle) > 128:
                return False

            launch_server_time_ms = self._server_time_ms()
            record = dict(normalized)
            record.update({
                "projectile_id": projectile_id,
                "source_vehicle": source_vehicle,
                "team": int(team),
                "launch_server_time_ms": launch_server_time_ms,
                "checked_through_ms": 0,
                "checked_distance": 0.0,
                "piercing_loss": 0.0,
                "launch_fingerprint": launch_fingerprint,
                "last_progress_fingerprint": None,
            })
            self.projectiles[projectile_id] = record
            if shooter_kind == "player":
                shooter.fire_seq = shot_seq
                shooter.shell_index = shell_index
            else:
                self.bot_pending_projectile_launches.discard(
                    (shooter_id, shot_seq))
            self.projectile_revision += 1

            horizontal = math.hypot(velocity[0], velocity[2])
            shot_yaw = math.atan2(velocity[0], velocity[2])
            # Canonical launch events publish physical vector elevation:
            # positive is upward.  Rendered gun pitch uses the opposite sign,
            # but RemoteVehicle explicitly adapts between the two contracts.
            shot_pitch = math.atan2(velocity[1], horizontal)
            event = {
                "kind": "shot" if shooter_kind == "player" else "bot_shot",
                "projectile_id": projectile_id,
                "shot_seq": shot_seq,
                "shell_index": shell_index,
                "origin": list(origin),
                "velocity": list(velocity),
                "gravity": gravity,
                "maxDistance": max_distance,
                "max_time_ms": max_time_ms,
                "is_he": is_he,
                "splash_radius": splash_radius,
                "penetration_factor": penetration_factor,
                "launch_server_time_ms": launch_server_time_ms,
                "shooter_kind": shooter_kind,
                "shooter_id": shooter_id,
                "source_vehicle": source_vehicle,
                "authority_epoch": self.authority_epoch,
                "shot_yaw": round(
                    ((shot_yaw + math.pi) % (2.0 * math.pi)) - math.pi, 6),
                "shot_pitch": round(_clamp(shot_pitch, -math.pi, math.pi), 6),
            }
            event["attacker" if shooter_kind == "player"
                  else "attacker_bot"] = shooter_id
            self.pending_events.append(event)
            return True

    def _normalize_projectile_cursor(self, raw, record):
        allowed = {
            "projectile_id", "base_checked_ms", "checked_through_ms",
            "checked_distance", "piercing_loss", "penetration_factor",
            "destructibles",
        }
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise ValueError("invalid cursor shape")
        projectile_id = raw.get("projectile_id")
        if (not isinstance(projectile_id, str) or not projectile_id or
                len(projectile_id) > 96 or
                projectile_id != record["projectile_id"]):
            raise ValueError("invalid projectile id")
        base_checked_ms = _exact_int(raw.get("base_checked_ms"), 0)
        checked_through_ms = _exact_int(
            raw.get("checked_through_ms"), base_checked_ms,
            record["max_time_ms"])
        checked_distance = round(_bounded_float(
            raw.get("checked_distance"), record["checked_distance"],
            record["max_distance"] + PROJECTILE_TOLERANCE), 6)
        piercing_loss = round(_bounded_float(
            raw.get("piercing_loss"), record["piercing_loss"], 100000.0), 6)
        penetration_factor = round(_bounded_float(
            raw.get("penetration_factor"), 0.0, 100.0), 6)
        if (penetration_factor >
                record["penetration_factor"] + PROJECTILE_TOLERANCE):
            raise ValueError("penetration factor increased")
        destructibles = self._normalize_projectile_destructibles(
            raw.get("destructibles"))
        return {
            "projectile_id": projectile_id,
            "base_checked_ms": base_checked_ms,
            "checked_through_ms": checked_through_ms,
            "checked_distance": checked_distance,
            "piercing_loss": piercing_loss,
            "penetration_factor": penetration_factor,
            "destructibles": destructibles,
        }

    def progress_projectiles(self, player_id, message):
        """Advance an authority-owned batch with cursor compare-and-swap."""
        with self.lock:
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    not self._projectile_message_fits(message) or
                    not self._projectile_authority_matches(
                        player_id, message)):
                return False
            if set(message) != {
                    "type", "round_id", "authority_epoch", "cursors"}:
                return False
            cursors = message.get("cursors")
            if (not isinstance(cursors, list) or not cursors or
                    len(cursors) > PROJECTILE_MAX_PROGRESS_BATCH):
                return False
            now_ms = self._server_time_ms()
            proposals = []
            seen = set()
            receipt_count = 0
            try:
                for raw in cursors:
                    projectile_id = raw.get("projectile_id") \
                        if isinstance(raw, dict) else None
                    if projectile_id in seen:
                        raise ValueError("duplicate cursor")
                    seen.add(projectile_id)
                    record = self.projectiles.get(projectile_id)
                    if record is None:
                        raise ValueError("unknown projectile")
                    cursor = self._normalize_projectile_cursor(raw, record)
                    receipt_count += len(cursor["destructibles"])
                    if receipt_count > PROJECTILE_MAX_DESTRUCTIBLES:
                        raise ValueError(
                            "too many projectile destructible receipts")
                    fingerprint = _message_fingerprint(cursor)
                    if cursor["base_checked_ms"] != record[
                            "checked_through_ms"]:
                        if record.get(
                                "last_progress_fingerprint") == fingerprint:
                            proposals.append((record, cursor, fingerprint, True))
                            continue
                        raise ValueError("cursor compare-and-swap failed")
                    elapsed = max(
                        0, now_ms - record["launch_server_time_ms"])
                    if (cursor["checked_through_ms"] >
                            elapsed + PROJECTILE_CLOCK_LEEWAY_MS):
                        raise ValueError("cursor is ahead of server time")
                    proposals.append((record, cursor, fingerprint, False))
            except (AttributeError, TypeError, ValueError, OverflowError):
                return False

            changed = False
            destructibles = []
            for record, cursor, fingerprint, repeated in proposals:
                if repeated:
                    continue
                destructibles.extend(cursor["destructibles"])
                record["checked_through_ms"] = cursor["checked_through_ms"]
                record["checked_distance"] = cursor["checked_distance"]
                record["piercing_loss"] = cursor["piercing_loss"]
                record["penetration_factor"] = cursor["penetration_factor"]
                record["last_progress_fingerprint"] = fingerprint
                changed = True
            self._commit_projectile_destructibles(
                player_id, destructibles)
            if changed:
                self.projectile_revision += 1
            return True

    def _normalize_projectile_effect(self, raw, record, impact, splash):
        allowed = {
            "target_kind", "target_id", "damage", "shot_result",
            "x", "y", "z", "critical",
            "critical_target_base_revision", "critical_target_ack_seq",
            "hull_damage",
        }
        required = {
            "target_kind", "target_id", "damage", "shot_result",
            "x", "y", "z",
        }
        if (not isinstance(raw, dict) or set(raw) - allowed or
                not required.issubset(raw)):
            raise ValueError("invalid effect shape")
        target_kind = raw.get("target_kind")
        if target_kind not in ("player", "bot"):
            raise ValueError("invalid target kind")
        target_id = _exact_int(
            raw.get("target_id"), 1, PROJECTILE_MAX_ID)
        damage = _exact_int(raw.get("damage"), 0, 5000)
        shot_result = _exact_int(raw.get("shot_result"), 0, 2)
        pose = _bounded_vector(
            [raw.get("x"), raw.get("y"), raw.get("z")],
            (-5000.0, -1000.0, -5000.0),
            (5000.0, 3000.0, 5000.0))
        target = (self.players.get(target_id) if target_kind == "player"
                  else self.bot_states.get(target_id))
        if target is None:
            raise ValueError("unknown target")
        target_team = (target.team if target_kind == "player"
                       else int(target.get("team", 0)))
        target_alive = (target.alive if target_kind == "player"
                        else bool(target.get("alive")))
        target_position = (
            (target.x, target.y, target.z) if target_kind == "player" else
            (float(target.get("x", 0.0)), float(target.get("y", 0.0)),
             float(target.get("z", 0.0))))
        if splash:
            distance = math.sqrt(sum(
                (target_position[index] - impact[index]) ** 2
                for index in range(3)))
            if distance > record["splash_radius"] + PROJECTILE_TOLERANCE:
                raise ValueError("splash target outside blast radius")
        elif (target_kind == record["shooter_kind"] and
              target_id == record["shooter_id"]):
            raise ValueError("direct self hit")

        critical = _critical_payload(raw.get("critical"))
        critical_accepted = True
        hull_damage = None
        if critical is not None:
            if not {"critical_target_base_revision",
                    "critical_target_ack_seq", "hull_damage"}.issubset(raw):
                raise ValueError("critical tokens missing")
            expected_base = (
                target.critical_report_base_revision
                if target_kind == "player" else
                int(target.get("combat_base_revision", 0)))
            expected_ack = (
                target.critical_ack_seq if target_kind == "player" else
                int(target.get("combat_ack_seq", 0)))
            hull_damage, critical_accepted = _critical_proposal_admission(
                raw, expected_base, expected_ack)
        elif set(raw) & {"critical_target_base_revision",
                         "critical_target_ack_seq", "hull_damage"}:
            raise ValueError("critical tokens without critical payload")
        return {
            "target_kind": target_kind, "target_id": target_id,
            "target": target, "target_team": target_team,
            "target_alive": target_alive, "damage": damage,
            "shot_result": shot_result, "pose": pose,
            "critical": critical,
            "critical_accepted": critical_accepted,
            "hull_damage": hull_damage, "splash": bool(splash),
        }

    def _apply_projectile_effect(self, record, proposal):
        target_kind = proposal["target_kind"]
        target_id = proposal["target_id"]
        target = proposal["target"]
        was_alive = proposal["target_alive"]
        critical = proposal["critical"]
        admitted_critical = (
            critical if proposal["critical_accepted"] and was_alive else None)
        damage = proposal["damage"]
        if critical is not None and not proposal["critical_accepted"]:
            damage = proposal["hull_damage"]
        if not was_alive:
            damage = 0

        if target_kind == "player":
            critical_before = target.critical
            applied = min(damage, target.health)
            target.health -= applied
            target.alive = target.health > 0
            target.display_health = target.health
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical)
            health = target.health
            alive = target.alive
        else:
            combat_before = self._bot_combat_signature(target)
            critical_before = combat_before[2]
            applied = min(damage, int(target.get("health", 0)))
            target["health"] = int(target.get("health", 0)) - applied
            target["alive"] = target["health"] > 0
            target["display_health"] = target["health"]
            if admitted_critical is not None:
                target["critical"] = _critical_state(admitted_critical)
                before_fire = bool(
                    critical_before and critical_before.get("fire", False))
                after_fire = bool(target["critical"].get("fire", False))
                if not before_fire and after_fire:
                    target["fire_attacker_kind"] = record["shooter_kind"]
                    target["fire_attacker_id"] = record["shooter_id"]
            self._commit_external_bot_combat(target, combat_before)
            critical_commit = ({
                "combat_revision": target.get("combat_revision", 0),
                "combat_base_revision": target.get(
                    "combat_base_revision", 0),
                "combat_ack_seq": target.get("combat_ack_seq", 0),
            } if critical is not None else None)
            health = int(target["health"])
            alive = bool(target["alive"])

        if (applied > 0 or _critical_damage_transition(
                critical_before, admitted_critical)):
            self._drop_capture_for_vehicle(target_kind, target_id)
        if record["shooter_kind"] == "player":
            event_kind = "hit" if target_kind == "player" else "bot_hit"
            attacker_key = "attacker"
        else:
            event_kind = ("bot_human_hit" if target_kind == "player"
                          else "bot_bot_hit")
            attacker_key = "attacker_bot"
        event = {
            "kind": event_kind,
            attacker_key: record["shooter_id"],
            "target" if target_kind == "player" else "target_bot": target_id,
            "projectile_id": record["projectile_id"],
            "shot_seq": record["shot_seq"],
            "shell_index": record["shell_index"],
            "shot_result": proposal["shot_result"],
            "damage": applied, "health": health, "dead": not alive,
            "attack_reason": 0, "death_reason": 0,
            "source": "shot", "splash": proposal["splash"],
            "world_pose": True,
            "x": proposal["pose"][0], "y": proposal["pose"][1],
            "z": proposal["pose"][2],
        }
        if critical is not None:
            event["critical_accepted"] = bool(
                proposal["critical_accepted"] and was_alive)
            if admitted_critical is not None:
                event["critical"] = admitted_critical
                if critical_commit:
                    event.update(critical_commit)
            else:
                event["critical_reject_reason"] = (
                    "target_destroyed" if not was_alive else
                    "stale_target_state")
                if critical_commit:
                    event.update(critical_commit)
        self.pending_events.append(event)
        if was_alive and not alive:
            if target_kind == "player":
                target.death_attacker_kind = record["shooter_kind"]
                target.death_attacker_id = record["shooter_id"]
            else:
                target["death_attacker_kind"] = record["shooter_kind"]
                target["death_attacker_id"] = record["shooter_id"]
            self._record_frag(
                record["shooter_kind"], record["shooter_id"],
                proposal["target_team"], target_kind, target_id)

    def resolve_projectile(self, player_id, message):
        """Validate one whole terminal effect batch before applying any HP."""
        with self.lock:
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    not self._projectile_message_fits(message) or
                    not self._projectile_authority_matches(
                        player_id, message)):
                return False
            allowed = {
                "type", "round_id", "authority_epoch", "projectile_id",
                "base_checked_ms", "outcome", "resolved_time_ms",
                "checked_distance", "piercing_loss", "penetration_factor",
                "impact", "direct", "splash", "destructibles",
            }
            if set(message) != allowed:
                return False
            projectile_id = message.get("projectile_id")
            if (not isinstance(projectile_id, str) or not projectile_id or
                    len(projectile_id) > 96):
                return False
            request_fingerprint = _message_fingerprint(message)
            terminal = self.projectile_tombstones.get(projectile_id)
            if terminal is not None:
                return terminal.get(
                    "request_fingerprint") == request_fingerprint
            record = self.projectiles.get(projectile_id)
            if record is None:
                return False
            try:
                base_checked_ms = _exact_int(
                    message.get("base_checked_ms"), 0)
                if base_checked_ms != record["checked_through_ms"]:
                    raise ValueError("cursor compare-and-swap failed")
                outcome = message.get("outcome")
                if outcome not in ("impact", "miss", "expired"):
                    raise ValueError("invalid outcome")
                resolved_time_ms = _exact_int(
                    message.get("resolved_time_ms"), base_checked_ms,
                    record["max_time_ms"])
                now_elapsed = max(
                    0, self._server_time_ms() -
                    record["launch_server_time_ms"])
                if resolved_time_ms > now_elapsed + PROJECTILE_CLOCK_LEEWAY_MS:
                    raise ValueError("resolution is ahead of server time")
                checked_distance = round(_bounded_float(
                    message.get("checked_distance"),
                    record["checked_distance"],
                    record["max_distance"] + PROJECTILE_TOLERANCE), 6)
                piercing_loss = round(_bounded_float(
                    message.get("piercing_loss"), record["piercing_loss"],
                    100000.0), 6)
                penetration_factor = round(_bounded_float(
                    message.get("penetration_factor"), 0.0, 100.0), 6)
                if (penetration_factor >
                        record["penetration_factor"] + PROJECTILE_TOLERANCE):
                    raise ValueError("penetration factor increased")
                direct_raw = message.get("direct")
                splash_raw = message.get("splash")
                if not isinstance(splash_raw, list):
                    raise ValueError("splash must be a list")
                if len(splash_raw) > PROJECTILE_MAX_SPLASH_TARGETS:
                    raise ValueError("too many splash targets")
                if not record["is_he"] and splash_raw:
                    raise ValueError("AP projectile cannot splash")
                impact = None
                if outcome == "impact":
                    impact = _bounded_vector(
                        message.get("impact"),
                        (-5000.0, -1000.0, -5000.0),
                        (5000.0, 3000.0, 5000.0))
                else:
                    if (message.get("impact") is not None or
                            direct_raw is not None or splash_raw):
                        raise ValueError("non-impact outcome has effects")
                    impact = None
                if direct_raw is not None and outcome != "impact":
                    raise ValueError("direct effect without impact")
                direct = (self._normalize_projectile_effect(
                    direct_raw, record, impact, False)
                          if direct_raw is not None else None)
                splash = [self._normalize_projectile_effect(
                    raw, record, impact, True) for raw in splash_raw]
                target_keys = []
                if direct is not None:
                    target_keys.append((direct["target_kind"],
                                        direct["target_id"]))
                target_keys.extend((proposal["target_kind"],
                                    proposal["target_id"])
                                   for proposal in splash)
                if len(target_keys) != len(set(target_keys)):
                    raise ValueError("duplicate direct or splash target")
                destructibles = self._normalize_projectile_destructibles(
                    message.get("destructibles"))
            except (TypeError, ValueError, OverflowError):
                return False

            impact_event = {
                "kind": "projectile_impact",
                "projectile_id": projectile_id,
                "outcome": outcome,
                "resolved_time_ms": resolved_time_ms,
                "checked_distance": checked_distance,
                "piercing_loss": piercing_loss,
                "penetration_factor": penetration_factor,
                "shooter_kind": record["shooter_kind"],
                "shooter_id": record["shooter_id"],
                "shot_seq": record["shot_seq"],
            }
            if impact is not None:
                impact_event["impact"] = list(impact)
            self._commit_projectile_destructibles(
                player_id, destructibles)
            self.pending_events.append(impact_event)
            if direct is not None:
                self._apply_projectile_effect(record, direct)
            for proposal in splash:
                self._apply_projectile_effect(record, proposal)
            self.projectiles.pop(projectile_id, None)
            self.projectile_tombstones[projectile_id] = {
                "projectile_id": projectile_id,
                "outcome": outcome,
                "launch_fingerprint": record["launch_fingerprint"],
                "request_fingerprint": request_fingerprint,
            }
            self.projectile_revision += 1
            self._maybe_finish_battle()
            return True

    def _expire_projectiles(self):
        if self.client_build != CLIENT_BUILD_0922:
            return 0
        if (self.server_authority is not None and
                self.server_authority.started()):
            # The bounded server manager owns these terminals. A wall-clock
            # expiry here could retire a shot while its final collision chord
            # is still queued behind other projectiles.
            return 0
        now_ms = self._server_time_ms()
        expired = []
        for projectile_id, record in self.projectiles.items():
            if now_ms >= (record["launch_server_time_ms"] +
                          record["max_time_ms"]):
                expired.append((projectile_id, record))
        for projectile_id, record in expired:
            self.projectiles.pop(projectile_id, None)
            self.projectile_tombstones[projectile_id] = {
                "projectile_id": projectile_id,
                "outcome": "expired",
                "launch_fingerprint": record["launch_fingerprint"],
                "request_fingerprint": None,
            }
            self.pending_events.append({
                "kind": "projectile_impact",
                "projectile_id": projectile_id,
                "outcome": "expired",
                "resolved_time_ms": record["max_time_ms"],
                "checked_distance": record["checked_distance"],
                "piercing_loss": record["piercing_loss"],
                "penetration_factor": record["penetration_factor"],
                "shooter_kind": record["shooter_kind"],
                "shooter_id": record["shooter_id"],
                "shot_seq": record["shot_seq"],
            })
        if expired:
            self.projectile_revision += 1
        return len(expired)

    def _prune_orphaned_bot_launch_edges(self):
        if not self.bot_pending_projectile_launches:
            return
        keep = set()
        for bot_id, shot_seq in self.bot_pending_projectile_launches:
            state = self.bot_states.get(bot_id)
            if (state is not None and state.get("alive") and
                    int(state.get("fire_seq", 0)) == int(shot_seq)):
                keep.add((bot_id, shot_seq))
        self.bot_pending_projectile_launches = keep

    def report_bot_hit(self, player_id, message):
        """Apply a human or authority-owned bot shot to a bot HP record."""
        with self.lock:
            self._clear_protocol_reject("bot_hit")
            if self.client_build == CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    "bot_hit", "legacy_projectile_path",
                    "#1513 requires projectile_resolve")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    "bot_hit", "round",
                    "round=%s server_round=%s" % (
                        message.get("round_id") if isinstance(message, dict)
                        else None, self.round_id))
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    "bot_hit", "combat_closed",
                    "phase=%s tick=%s" % (self.phase, self.tick))
            if self.battle_result is not None:
                return self._set_protocol_reject(
                    "bot_hit", "battle_finished", "battle_result=set")
            if not all(key in message for key in
                       ("target", "shot_seq", "damage")):
                return self._set_protocol_reject(
                    "bot_hit", "message_shape",
                    "required=target,shot_seq,damage")
            if (not _has_finite_fields(
                    message, ("target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return self._set_protocol_reject(
                    "bot_hit", "message_values",
                    "target=%s seq=%s damage=%s" % (
                        message.get("target"), message.get("shot_seq"),
                        message.get("damage")))
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError as error:
                return self._set_protocol_reject(
                    "bot_hit", "critical_payload", "reason=%s" % error)
            try:
                shot_seq = int(message.get("shot_seq", 0))
                bot_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return self._set_protocol_reject(
                    "bot_hit", "identity",
                    "target=%s seq=%s" % (
                        message.get("target"), message.get("shot_seq")))
            state = self.bot_states.get(bot_id)
            if state is None or not state.get("alive"):
                return self._set_protocol_reject(
                    "bot_hit", "target_unavailable",
                    "target=%s known=%s alive=%s" % (
                        bot_id, state is not None,
                        bool(state and state.get("alive"))))
            combat_before = self._bot_combat_signature(state)
            attacker_bot_value = message.get("attacker_bot")
            if attacker_bot_value is not None:
                if player_id != self.bot_authority_id:
                    return self._set_protocol_reject(
                        "bot_hit", "authority",
                        "sender=%s authority=%s" % (
                            player_id, self.bot_authority_id))
                if player_id != self.bot_manifest_authority_id:
                    return self._set_protocol_reject(
                        "bot_hit", "manifest_authority",
                        "sender=%s manifest_authority=%s" % (
                            player_id, self.bot_manifest_authority_id))
                try:
                    attacker_bot_id = int(attacker_bot_value)
                except (TypeError, ValueError):
                    return self._set_protocol_reject(
                        "bot_hit", "attacker_id",
                        "attacker_bot=%s" % attacker_bot_value)
                attacker_bot = self.bot_states.get(attacker_bot_id)
                splash = bool(message.get("splash", False))
                hit_key = (("bot_shot", attacker_bot_id, shot_seq,
                            "bot", bot_id) if splash else
                           ("bot_shot", attacker_bot_id, shot_seq))
                if attacker_bot is None or not attacker_bot.get("alive"):
                    return self._set_protocol_reject(
                        "bot_hit", "attacker_unavailable",
                        "attacker_bot=%s known=%s alive=%s" % (
                            attacker_bot_id, attacker_bot is not None,
                            bool(attacker_bot and attacker_bot.get("alive"))))
                if attacker_bot_id == bot_id and not splash:
                    return self._set_protocol_reject(
                        "bot_hit", "self_hit",
                        "attacker_bot=%s target=%s splash=false" % (
                            attacker_bot_id, bot_id))
                server_fire_seq = int(attacker_bot.get("fire_seq", 0))
                if shot_seq <= 0 or shot_seq > server_fire_seq:
                    return self._set_protocol_reject(
                        "bot_hit", "shot_lineage",
                        ("attacker_bot=%s target=%s client_fire=%s "
                         "server_fire=%s client_target_base=%s "
                         "server_target_base=%s client_target_ack=%s "
                         "server_target_ack=%s") % (
                            attacker_bot_id, bot_id, shot_seq,
                            server_fire_seq,
                            message.get("critical_target_base_revision"),
                            state.get("combat_base_revision"),
                            message.get("critical_target_ack_seq"),
                            state.get("combat_ack_seq")))
                if hit_key in self.bot_reported_hits:
                    return self._set_protocol_reject(
                        "bot_hit", "duplicate",
                        "attacker_bot=%s target=%s seq=%s splash=%s" % (
                            attacker_bot_id, bot_id, shot_seq, splash))
                distance = math.hypot(
                    state["x"] - attacker_bot["x"],
                    state["z"] - attacker_bot["z"])
                if distance > 5000.0:
                    return self._set_protocol_reject(
                        "bot_hit", "distance",
                        "attacker_bot=%s target=%s distance=%.3f" % (
                            attacker_bot_id, bot_id, distance))
                reported_hits = self.bot_reported_hits
                attacker_id = attacker_bot_id
                shell_index = attacker_bot.get("shell_index", 0)
                event_kind = "bot_bot_hit"
            else:
                attacker = self.players.get(player_id)
                splash = bool(message.get("splash", False))
                hit_key = (("shot", shot_seq, "bot", bot_id)
                           if splash else ("shot", shot_seq))
                if attacker is None or not attacker.alive:
                    return self._set_protocol_reject(
                        "bot_hit", "attacker_unavailable",
                        "attacker=%s known=%s alive=%s" % (
                            player_id, attacker is not None,
                            bool(attacker and attacker.alive)))
                if shot_seq <= 0 or shot_seq > attacker.fire_seq:
                    return self._set_protocol_reject(
                        "bot_hit", "shot_lineage",
                        ("attacker=%s target=%s client_fire=%s "
                         "server_fire=%s client_target_base=%s "
                         "server_target_base=%s client_target_ack=%s "
                         "server_target_ack=%s") % (
                            player_id, bot_id, shot_seq, attacker.fire_seq,
                            message.get("critical_target_base_revision"),
                            state.get("combat_base_revision"),
                            message.get("critical_target_ack_seq"),
                            state.get("combat_ack_seq")))
                if hit_key in attacker.reported_hits:
                    return self._set_protocol_reject(
                        "bot_hit", "duplicate",
                        "attacker=%s target=%s seq=%s splash=%s" % (
                            player_id, bot_id, shot_seq, splash))
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
                except ValueError as error:
                    return self._set_protocol_reject(
                        "bot_hit", "critical_contract",
                        ("target=%s client_base=%s server_base=%s "
                         "client_ack=%s server_ack=%s reason=%s") % (
                            bot_id,
                            message.get("critical_target_base_revision"),
                            state.get("combat_base_revision"),
                            message.get("critical_target_ack_seq"),
                            state.get("combat_ack_seq"), error))
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
            capture_reset = bool(
                applied > 0 or _critical_damage_transition(
                    combat_before[2], admitted_critical))
            if capture_reset:
                self._drop_capture_for_vehicle("bot", bot_id)
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
            self._clear_protocol_reject("bot_human_hit")
            if self.client_build == CLIENT_BUILD_0922:
                return self._set_protocol_reject(
                    "bot_human_hit", "legacy_projectile_path",
                    "#1513 requires projectile_resolve")
            if not self._message_round_matches(message):
                return self._set_protocol_reject(
                    "bot_human_hit", "round",
                    "round=%s server_round=%s" % (
                        message.get("round_id") if isinstance(message, dict)
                        else None, self.round_id))
            if not self._combat_accepting():
                return self._set_protocol_reject(
                    "bot_human_hit", "combat_closed",
                    "phase=%s tick=%s" % (self.phase, self.tick))
            if self.battle_result is not None:
                return self._set_protocol_reject(
                    "bot_human_hit", "battle_finished",
                    "battle_result=set")
            if player_id != self.bot_authority_id:
                return self._set_protocol_reject(
                    "bot_human_hit", "authority",
                    "sender=%s authority=%s" % (
                        player_id, self.bot_authority_id))
            if player_id != self.bot_manifest_authority_id:
                return self._set_protocol_reject(
                    "bot_human_hit", "manifest_authority",
                    "sender=%s manifest_authority=%s" % (
                        player_id, self.bot_manifest_authority_id))
            if not all(key in message for key in
                       ("attacker_bot", "target", "shot_seq", "damage")):
                return self._set_protocol_reject(
                    "bot_human_hit", "message_shape",
                    "required=attacker_bot,target,shot_seq,damage")
            if (not _has_finite_fields(
                    message, ("attacker_bot", "target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return self._set_protocol_reject(
                    "bot_human_hit", "message_values",
                    "attacker_bot=%s target=%s seq=%s damage=%s" % (
                        message.get("attacker_bot"), message.get("target"),
                        message.get("shot_seq"), message.get("damage")))
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError as error:
                return self._set_protocol_reject(
                    "bot_human_hit", "critical_payload",
                    "reason=%s" % error)
            try:
                bot_id = int(message.get("attacker_bot", 0))
                target_id = int(message.get("target", 0))
                shot_seq = int(message.get("shot_seq", 0))
            except (TypeError, ValueError):
                return self._set_protocol_reject(
                    "bot_human_hit", "identity",
                    "attacker_bot=%s target=%s seq=%s" % (
                        message.get("attacker_bot"), message.get("target"),
                        message.get("shot_seq")))
            bot = self.bot_states.get(bot_id)
            target = self.players.get(target_id)
            if bot is None or not bot.get("alive") or target is None or not target.alive:
                return self._set_protocol_reject(
                    "bot_human_hit", "vehicle_unavailable",
                    ("attacker_bot=%s known=%s alive=%s target=%s "
                     "known=%s alive=%s") % (
                        bot_id, bot is not None,
                        bool(bot and bot.get("alive")), target_id,
                        target is not None,
                        bool(target and target.alive)))
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
                code = ("duplicate" if hit_key in self.bot_reported_hits
                        else "shot_lineage")
                return self._set_protocol_reject(
                    "bot_human_hit", code,
                    ("attacker_bot=%s target=%s client_fire=%s "
                     "server_fire=%s client_target_base=%s "
                     "server_target_base=%s client_target_ack=%s "
                     "server_target_ack=%s duplicate=%s") % (
                        bot_id, target_id, shot_seq, bot_fire_seq,
                        message.get("critical_target_base_revision"),
                        target.critical_report_base_revision,
                        message.get("critical_target_ack_seq"),
                        target.critical_ack_seq,
                        hit_key in self.bot_reported_hits))
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
                except ValueError as error:
                    return self._set_protocol_reject(
                        "bot_human_hit", "critical_contract",
                        ("target=%s client_base=%s server_base=%s "
                         "client_ack=%s server_ack=%s reason=%s") % (
                            target_id,
                            message.get("critical_target_base_revision"),
                            target.critical_report_base_revision,
                            message.get("critical_target_ack_seq"),
                            target.critical_ack_seq, error))
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
            critical_before = target.critical
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical)
            capture_reset = bool(
                applied > 0 or _critical_damage_transition(
                    critical_before, admitted_critical))
            if capture_reset:
                self._drop_capture_for_vehicle("player", target_id)
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
            if applied_bot > 0:
                self._drop_capture_for_vehicle("bot", bot_id)
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
                if applied_target > 0:
                    self._drop_capture_for_vehicle("bot", target_id)
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
                if applied_target > 0:
                    self._drop_capture_for_vehicle("player", target_id)
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
        if self.projectiles:
            for projectile_id, record in list(self.projectiles.items()):
                self.projectile_tombstones[projectile_id] = {
                    "projectile_id": projectile_id,
                    "outcome": "battle_finished",
                    "launch_fingerprint": record["launch_fingerprint"],
                    "request_fingerprint": None,
                }
            self.projectiles.clear()
            self.projectile_revision += 1
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
                player.shell_index = max(0, min(int(message.get("shell_index", player.shell_index)), 9))
            except (TypeError, ValueError):
                pass
            if self.client_build != CLIENT_BUILD_0922:
                try:
                    fire_seq = int(message.get("fire_seq", player.fire_seq))
                except (TypeError, ValueError):
                    fire_seq = player.fire_seq
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
                    self._combat_accepting() and player.alive):
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
        if not player.alive:
            return False
        try:
            critical = _critical_payload(message.get("reported_critical"))
        except ValueError:
            return False
        health = max(0, min(int(_finite_float(
            message.get("reported_health"), player.health)),
            player.max_health))
        health = min(health, player.health)
        stored_critical = _critical_state(critical)
        critical_before = player.critical
        old_discrete = _critical_discrete_state(player.critical)
        new_discrete = _critical_discrete_state(stored_critical)
        critical_damage = _critical_damage_transition(
            critical_before, critical)
        critical_event_changed = (
            stored_critical is not None and
            (new_discrete != old_discrete or bool(critical.get("events")) or
             critical_damage))
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
        capture_reset = bool(damage > 0 or critical_damage)
        if capture_reset:
            self._drop_capture_for_vehicle("player", player.player_id)
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
        reported_attacker_kind = None
        reported_attacker_id = 0
        if attacker_id in self.players:
            reported_attacker_kind = "player"
            reported_attacker_id = attacker_id
        elif attacker_bot in self.bot_states:
            reported_attacker_kind = "bot"
            reported_attacker_id = attacker_bot
        # The owner may retain the last attacker so a locally simulated fatal
        # tick can preserve the death ledger.  That attribution is ledger-only:
        # client_simulation is an explicit non-attack wire cause and must never
        # expose attacker fields to the server or client event validators.
        self.pending_events.append(event)
        if (was_alive and not player.alive and
                reported_attacker_kind is not None):
            player.death_attacker_kind = reported_attacker_kind
            player.death_attacker_id = int(reported_attacker_id)
            self._record_frag(
                reported_attacker_kind, reported_attacker_id, player.team,
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
            if self.client_build == CLIENT_BUILD_0922:
                return False
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
            critical_before = target.critical
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical)
            capture_reset = bool(
                applied_damage > 0 or _critical_damage_transition(
                    critical_before, admitted_critical))
            if capture_reset:
                self._drop_capture_for_vehicle("player", target_id)
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

    @staticmethod
    def _capture_vehicle_key(kind, vehicle_id):
        return "%s:%d" % ("human" if kind == "player" else "bot",
                           int(vehicle_id))

    def _drop_capture_for_vehicle(self, kind, vehicle_id):
        """Drop only one damaged vehicle's accumulated capture points."""
        if self.client_build != CLIENT_BUILD_0922:
            return 0
        key = self._capture_vehicle_key(kind, vehicle_id)
        dropped_total = 0
        for base_team in (1, 2):
            state = self.rules_state["bases"][str(base_team)]
            contributors = self.capture_contributors[base_team]
            dropped_total += max(0, int(contributors.pop(key, 0) or 0))
            state["points"] = min(100, sum(
                max(0, int(points or 0))
                for points in contributors.values()))
            if not contributors:
                self.capture_cursors[base_team] = 0
            rate = min(max(0, int(state.get("invaders", 0))), 3)
            state["time_left"] = (
                float(max(0, 100 - state["points"])) / float(rate)
                if rate > 0 else 0.0)
        return dropped_total

    def _update_capture(self):
        """Copy the 0.8.2 standard-mode 50 m, 1 Hz capture law."""
        if (not self._combat_accepting() or
                self.tick % max(1, int(round(TICK_HZ))) != 0 or
                self.battle_result is not None):
            return False
        # #1513 navigation graphs contain packed CTF objective positions.  Its
        # tactical-map ``bases`` are route annotations and can be hundreds of
        # metres from the retail capture circles, so never use them as a modern
        # protocol fallback.
        bases = (self.capture_bases if self.client_build == CLIENT_BUILD_0922
                 else self.capture_bases or
                 (self._map_rule_data().get('bases') or {}))
        if not bases:
            return False
        vehicles = {1: [], 2: []}
        for player in self.players.values():
            if (player.connected and player.participating and player.alive and
                    (self.client_build != CLIENT_BUILD_0922 or
                     player.client_position) and player.team in vehicles):
                vehicles[player.team].append((
                    self._capture_vehicle_key("player", player.player_id),
                    player.x, player.z))
        for state in self.bot_states.values():
            team = int(state.get('team', 0))
            if (state.get('alive') and
                    (self.client_build != CLIENT_BUILD_0922 or
                     state.get('world_pose')) and
                    team in vehicles):
                vehicles[team].append((
                    self._capture_vehicle_key("bot", state['id']),
                    state['x'], state['z']))
        changed = False
        self.capture_threat_bases = {1: [], 2: []}
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
            threatened = []
            for index, (bx, bz) in enumerate(normalized):
                if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                       for unused_key, x, z in vehicles[invading_team]):
                    threatened.append({
                        "id": "%d:%d" % (base_team, index),
                        "x": round(bx, 3), "y": 0.0,
                        "z": round(bz, 3),
                    })
            self.capture_threat_bases[base_team] = threatened
            invader_keys = sorted(set(
                key for key, x, z in vehicles[invading_team]
                if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                       for bx, bz in normalized)))
            defenders = sum(
                1 for unused_key, x, z in vehicles[base_team]
                if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                       for bx, bz in normalized))
            state = self.rules_state['bases'][str(base_team)]
            previous = dict(state)
            if self.client_build != CLIENT_BUILD_0922:
                if invader_keys and defenders == 0:
                    state['points'] = min(
                        100, int(state.get('points', 0)) +
                        min(len(invader_keys), 3))
                elif not invader_keys:
                    state['points'] = 0
                state['stopped'] = defenders > 0
            else:
                contributors = self.capture_contributors[base_team]
                active = set(invader_keys)
                for vehicle_id in list(contributors):
                    if vehicle_id not in active:
                        contributors.pop(vehicle_id, None)
                for vehicle_id in invader_keys:
                    contributors.setdefault(vehicle_id, 0)
                points = min(100, sum(
                    max(0, int(value or 0))
                    for value in contributors.values()))
                state['stopped'] = bool(invader_keys and defenders > 0)
                if invader_keys and not state['stopped'] and points < 100:
                    cursor = (self.capture_cursors[base_team] %
                              len(invader_keys))
                    budget = min(3, len(invader_keys), 100 - points)
                    for offset in range(budget):
                        vehicle_id = invader_keys[
                            (cursor + offset) % len(invader_keys)]
                        contributors[vehicle_id] = int(
                            contributors.get(vehicle_id, 0) or 0) + 1
                    self.capture_cursors[base_team] = (
                        cursor + budget) % len(invader_keys)
                elif not invader_keys:
                    self.capture_cursors[base_team] = 0
                state['points'] = min(100, sum(
                    max(0, int(value or 0))
                    for value in contributors.values()))
            state['invaders'] = len(invader_keys)
            rate = min(len(invader_keys), 3)
            state['time_left'] = (
                float(max(0, 100 - state['points'])) / float(rate)
                if rate > 0 else 0.0)
            changed = changed or state != previous
            if state['points'] >= 100:
                self._finish_battle(
                    invading_team, 'base captured', base_team)
                break
        return changed

    def _bot_defense_context(self):
        """Return only the own-base pressure facts needed by BotPlanner."""
        contributors = {}
        for team in (1, 2):
            values = []
            for key in sorted(self.capture_contributors.get(team, {})):
                try:
                    kind, raw_id = str(key).split(":", 1)
                    vehicle_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if kind not in ("human", "bot") or vehicle_id <= 0:
                    continue
                values.append({"kind": kind, "id": vehicle_id})
            contributors[str(team)] = values
        return {
            "bases": dict((str(team), [dict(point) for point in
                                        self.capture_threat_bases.get(
                                            team, ())])
                          for team in (1, 2)),
            "states": dict((str(team), dict(
                self.rules_state["bases"][str(team)]))
                for team in (1, 2)),
            "contributors": contributors,
        }

    def tick_once(self, dt):
        reset_message = None
        had_pending_live = False
        failed_live_recipients = []
        authority_observation_relays = ()
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
            authority_fallback = self._expire_authority_prerequisite()
        if authority_fallback:
            self.broadcast_current_roster()
        with self.lock:
            if self.phase != "battle":
                return
            self.tick += 1
            self._prune_orphaned_bot_launch_edges()
            if (self.battle_result is None and
                    self.tick >= int(round(
                        (PREBATTLE_SECONDS + BATTLE_DURATION_SECONDS) *
                        TICK_HZ))):
                self._finish_battle(0, "battle_timeout", 0)
            self._update_capture()
            for player in list(self.players.values()):
                self._apply_movement(player, dt)
            if self.server_authority is not None:
                authority_observation_relays = (
                    self.server_authority.update(
                        dt, float(self.tick) / TICK_HZ,
                        live=(self.battle_result is None and
                              self._timing_payload()["phase"] == "battle"))
                    or ())
            self._expire_projectiles()
            if self.battle_result is None:
                self.bot_orders = self.bot_planner.build_orders(
                    self.bot_manifest, list(self.bot_states.values()),
                    [self._public_player(p) for p in self.players.values() if p.connected],
                    time.monotonic(), self._bot_defense_context())
            events = []
            for ordinal, pending in enumerate(self.pending_events):
                self._validate_combat_event_for_wire(pending)
                event = dict(pending)
                event["event_id"] = "%d:%d:%d" % (
                    self.round_id, self.tick, ordinal)
                events.append(event)
            self.pending_events = []
            tick_server_time_ms = None
            if self.client_build == CLIENT_BUILD_0922:
                # Events and the snapshot published by one simulation tick
                # share one current clock sample.  Reusing a prior snapshot's
                # time would make delayed projectile tracers start at the
                # wrong point on their authoritative trajectory.
                tick_server_time_ms = self._server_time_ms()
            snapshot = {
                "type": "snapshot",
                "protocol": PROTOCOL_VERSION,
                "server_tick": self.tick,
                "round_id": self.round_id,
                "map": self.map_name,
                "bot_authority_id": self.bot_authority_id,
                "players": [self._public_player(p) for p in self.players.values() if p.connected],
                "bots": [self.bot_states[key] for key in sorted(self.bot_states)],
                "bot_state_revision": self.bot_state_revision,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "timing": self._timing_payload(),
            }
            if self.client_build == CLIENT_BUILD_0922:
                snapshot.update({
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": tick_server_time_ms,
                    "projectile_revision": self.projectile_revision,
                    "projectiles": self._projectile_snapshot(),
                })
                snapshot.update(self._authority_fields())
            if self.server_authority is not None:
                authority_view = dict(snapshot)
                authority_view["bots"] = [
                    dict(value) for value in snapshot["bots"]]
                self.server_authority.apply_snapshot(authority_view)
            events_message = None
            if events:
                events_message = {
                    "type": "events",
                    "protocol": PROTOCOL_VERSION,
                    "round_id": self.round_id,
                    "server_tick": self.tick,
                    "events": events,
                }
                if self.client_build == CLIENT_BUILD_0922:
                    events_message.update({
                        "authority_epoch": self.authority_epoch,
                        "server_time_ms": tick_server_time_ms,
                    })
            recipients = list(self.players.values())
            bot_combat_logs = dict(
                (event["event_id"], _bot_combat_log_message(
                    event, self.players, self.bot_states))
                for event in events
                if event.get("kind") in (
                    "bot_hit", "bot_human_hit", "bot_bot_hit"))
        for relay in authority_observation_relays:
            self.broadcast_bot_observation(relay)
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
                    _server_log(bot_combat_logs[event["event_id"]])
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
                elif event.get("kind") == "projectile_impact":
                    _server_log(
                        "PROJECTILE TERMINAL id=%s outcome=%s elapsed_ms=%s" % (
                            event.get("projectile_id"),
                            event.get("outcome"),
                            event.get("resolved_time_ms")))
            self.broadcast(events_message)
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

    def broadcast_bot_observation(self, message):
        """Relay one validated modern observation to active round members."""
        with self.lock:
            if (self.client_build != CLIENT_BUILD_0922 or
                    not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    message.get("type") != "bot_observation"):
                return False
            players = tuple(
                player for player in self.players.values()
                if player.connected and player.participating)
        for player in players:
            if not player.send(message):
                self.remove_player(player.player_id)
        return True

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
                    "authority_epoch": self.authority_epoch,
                    "server_time_ms": self._server_time_ms(),
                    "bot_manifest": list(self.bot_manifest),
                    "bot_order_revision": self.bot_orders["revision"],
                    "bot_orders": list(self.bot_orders["orders"]),
                    "rules": self.rules_state,
                    "battle_result": self.battle_result,
                    "destructible_revision": self.destructible_revision,
                    "destructibles": list(self.destructibles.values()),
                })
                outgoing.update(self._authority_fields())
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
                    welcome_message = {
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
                    }
                    if server.state.client_build == CLIENT_BUILD_0922:
                        welcome_message.update({
                            "authority_epoch": server.state.authority_epoch,
                            "capabilities": list(player.capabilities),
                        })
                    welcomed = player.send(welcome_message)
            if player is None:
                messages = {
                    "battle_in_progress": "battle already in progress",
                    "full": "server is full",
                    "unsupported_client_build": "unsupported or missing client build",
                    "incompatible_client_build": "this room is using a different client build",
                    "map_not_available_for_client": "the fixed server map is unavailable in this client build",
                    "unsupported_capabilities": "required client capabilities are missing",
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
                    elif message_type == "projectile_launch":
                        if not server.state.launch_projectile(
                                player.player_id, message):
                            _server_log(
                                "PROJECTILE LAUNCH rejected sender=%d shooter=%s:%s seq=%s" % (
                                    player.player_id,
                                    message.get("shooter_kind"),
                                    message.get("shooter_id"),
                                    message.get("shot_seq")))
                    elif message_type == "projectile_progress":
                        if not server.state.progress_projectiles(
                                player.player_id, message):
                            _server_log(
                                "PROJECTILE PROGRESS rejected sender=%d epoch=%s count=%s" % (
                                    player.player_id,
                                    message.get("authority_epoch"),
                                    len(message.get("cursors", ()))
                                    if isinstance(message.get("cursors"), list)
                                    else None))
                    elif message_type == "projectile_resolve":
                        if not server.state.resolve_projectile(
                                player.player_id, message):
                            _server_log(
                                "PROJECTILE RESOLVE rejected sender=%d projectile=%s outcome=%s" % (
                                    player.player_id,
                                    message.get("projectile_id"),
                                    message.get("outcome")))
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
                        accepted = server.state.update_bot_states(
                            player.player_id, message)
                        if server.state.should_log_protocol_reject(
                                "bot_state", accepted):
                            _server_log(
                                "BOT STATE rejected authority=%d code=%s reason=%s" % (
                                    player.player_id,
                                    server.state.last_bot_state_reject_code,
                                    server.state.last_bot_state_reject))
                    elif message_type == "bot_observation":
                        relay = server.state.update_bot_observation(
                            player.player_id, message)
                        if isinstance(relay, dict):
                            server.state.broadcast_bot_observation(relay)
                    elif message_type == "bot_hit_report":
                        accepted = server.state.report_bot_hit(
                            player.player_id, message)
                        if server.state.should_log_protocol_reject(
                                "bot_hit", accepted):
                            _server_log(
                                ("BOT HIT rejected authority=%d attacker_bot=%s "
                                 "target=%s seq=%s code=%s reason=%s") % (
                                    player.player_id,
                                    message.get("attacker_bot"),
                                    message.get("target"),
                                    message.get("shot_seq"),
                                    server.state.last_bot_hit_reject_code,
                                    server.state.last_bot_hit_reject))
                    elif message_type == "bot_human_hit":
                        accepted = server.state.report_bot_human_hit(
                            player.player_id, message)
                        if server.state.should_log_protocol_reject(
                                "bot_human_hit", accepted):
                            _server_log(
                                ("BOT HUMAN HIT rejected authority=%d "
                                 "attacker_bot=%s target=%s seq=%s "
                                 "code=%s reason=%s") % (
                                    player.player_id,
                                    message.get("attacker_bot"),
                                    message.get("target"),
                                    message.get("shot_seq"),
                                    server.state.last_bot_human_hit_reject_code,
                                    server.state.last_bot_human_hit_reject))
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
                    elif message_type == "descriptor_catalog":
                        server.state.store_vehicle_catalog(
                            player.player_id, message)
                    elif message_type == "destructible_map":
                        installed = server.state.store_destructible_map(
                            player.player_id, message)
                        if installed == "ready":
                            loading_snapshot = server.state.loading_snapshot()
                            if loading_snapshot is not None:
                                server.state.broadcast_loading_transition(
                                    loading_snapshot)
                            live = server.state.activate_battle_if_ready()
                            if live is not None:
                                _server_log(
                                    "BATTLE LIVE round=%d countdown=%ss players=%d" % (
                                        live["round_id"],
                                        live["countdown_seconds"],
                                        len(server.state.players)))
                        elif installed == "failed":
                            server.state.broadcast_current_roster()
                    elif message_type == "descriptor_bundle":
                        accepted = server.state.donate_descriptors(
                            player.player_id, message)
                        if accepted == "started":
                            server.state.broadcast_loading_transition({
                                "type": "snapshot",
                                "round_id": server.state.round_id,
                            })
                            live = server.state.activate_battle_if_ready()
                            if live is not None:
                                _server_log(
                                    "BATTLE LIVE round=%d countdown=%ss players=%d" % (
                                        live["round_id"],
                                        live["countdown_seconds"],
                                        len(server.state.players)))
                        elif accepted == "failed":
                            server.state.broadcast_current_roster()
                    elif message_type == "select_vehicle":
                        if server.state.select_vehicle(
                                player.player_id, message):
                            _server_log("VEHICLE id=%d vehicle=%s hp=%d" % (
                                player.player_id, player.vehicle,
                                player.max_health))
                            server.state.broadcast_current_roster()
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


def run_server(host, port, map_name, max_players,
               authority_mode="client"):
    state = BattleState(map_name=map_name, max_players=max_players,
                        authority_mode=authority_mode)
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
    parser.add_argument(
        "--authority", dest="authority_mode",
        choices=("server", "client"),
        default=os.environ.get("WOT_LAN_AUTHORITY", "client"),
        help="who simulates bots in 0.9.22 rounds (default: client; "
             "WOT_LAN_AUTHORITY overrides)")
    args = parser.parse_args()
    run_server(args.host, args.port, args.map_name, args.max_players,
               args.authority_mode)


if __name__ == "__main__":
    main()
