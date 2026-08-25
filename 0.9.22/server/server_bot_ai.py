"""Pure-data tactical planner for the LAN bot authority.

The planner deliberately has no socket or BigWorld dependency. It imports only
the pure-data cover scorer shared with the client; all inputs and outputs are
JSON-compatible dictionaries so an eventual Go service can preserve the same
contract. Enemy data is accepted only through ``report_contacts``; players and
bot state are used to validate identities, never to invent a target position.
"""

import math
import os
import sys


_PORT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_CLIENT_SCRIPT_ROOT = os.path.join(
    _PORT_ROOT, 'src', 'res', 'scripts', 'client')
if _CLIENT_SCRIPT_ROOT not in sys.path:
    sys.path.insert(0, _CLIENT_SCRIPT_ROOT)

from gui.mods.offline_lan_0922.ai.cover import (
    normalize_candidate,
    score_candidates,
)


CONTACT_TTL_SECONDS = 8.0
MAX_CONTACTS_PER_TEAM = 32
COVER_TTL_SECONDS = 8.0
MAX_COVER_REPORTS = 16
MAX_COVER_CANDIDATES = 12
TARGET_LEASE_SECONDS = 2.0
TARGET_SWITCH_MARGIN = 3.0
CLOSE_THREAT_DISTANCE = 50.0
CLOSE_THREAT_SCORE_BONUS = 100.0
CLOSE_THREAT_FOCUS_LIMIT = 4
ROUTE_REBALANCE_SECONDS = 4.0
ROUTE_LEASE_SECONDS = 6.0
MAX_BASE_DEFENDERS = 3
RECENT_HIT_SECONDS = 6.0
RECENT_ATTACKER_SCORE_BONUS = 140.0
LOW_HEALTH_BASE_FRACTION = 0.18
CROSSFIRE_MIN_ANGLE = math.radians(55.0)
CROSSFIRE_MAX_DISTANCE = 360.0


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) else float(default)


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp(value, low, high):
    return max(low, min(high, value))


def _point(raw):
    raw = raw or {}
    return {
        "x": round(_clamp(_number(raw.get("x")), -2000.0, 2000.0), 3),
        "y": round(_clamp(_number(raw.get("y")), -1000.0, 1000.0), 3),
        "z": round(_clamp(_number(raw.get("z")), -2000.0, 2000.0), 3),
    }


class BotPlanner(object):
    """Server-side route, focus-fire, and last-contact order coordinator."""

    def __init__(self):
        self.revision = 0
        self._contacts = {1: {}, 2: {}}
        self._last_orders = None
        self._last_order_signature = None
        self._route_states = {}
        self._route_assignments = {}
        self._next_route_rebalance = {1: 0.0, 2: 0.0}
        self._engage_anchors = {}
        self._affordances = {}
        self._cover_states = {}
        self._cover_reservations = set()
        self._target_assignments = {}
        self._base_defense = {1: {}, 2: {}}
        self._artillery_anchors = {}
        self._recent_hits = {}

    def reset(self):
        self.revision = 0
        self._contacts = {1: {}, 2: {}}
        self._last_orders = None
        self._last_order_signature = None
        self._route_states = {}
        self._route_assignments = {}
        self._next_route_rebalance = {1: 0.0, 2: 0.0}
        self._engage_anchors = {}
        self._affordances = {}
        self._cover_states = {}
        self._cover_reservations = set()
        self._target_assignments = {}
        self._base_defense = {1: {}, 2: {}}
        self._artillery_anchors = {}
        self._recent_hits = {}

    def report_damage(self, victim_bot_id, attacker_kind, attacker_id,
                      damage, now):
        """Record one server-admitted hostile hit for tactical reactions."""
        victim_bot_id = _integer(victim_bot_id)
        attacker_id = _integer(attacker_id)
        damage = max(0, _integer(damage))
        kind = str(attacker_kind or "")
        if kind == "player":
            kind = "human"
        if (victim_bot_id <= 0 or attacker_id <= 0 or damage <= 0 or
                kind not in ("human", "bot")):
            return False
        self._recent_hits[victim_bot_id] = {
            "attacker": (kind, attacker_id),
            "reported_at": _number(now),
            "damage": damage,
        }
        return True

    def report_contacts(self, contacts, known_targets, now,
                        accepted_visibility=None):
        """Store only authority-reported observations after identity checks.

        ``known_targets`` maps an id to ``{"team": int, "alive": bool}``.
        Reporting a contact never looks up its target's live pose, which keeps
        this server from becoming omniscient.
        """
        accepted = 0
        if not isinstance(contacts, (list, tuple)):
            return accepted
        for raw in contacts[:MAX_CONTACTS_PER_TEAM * 2]:
            if not isinstance(raw, dict):
                continue
            # This package does not support mixed authority-client builds.
            # Missing or malformed per-bot firing-lane evidence must not fall
            # back to the old "every bot can shoot" interpretation.
            if ("shootable_by_bot_ids" not in raw or
                    not isinstance(raw.get("shootable_by_bot_ids"),
                                   (list, tuple)) or
                    "visible" not in raw or
                    not isinstance(raw.get("visible"), bool)):
                continue
            observing_team = _integer(raw.get("observing_team"))
            target_id = _integer(raw.get("target_id"))
            target_kind = str(raw.get("target_kind") or "")
            target = known_targets.get((target_kind, target_id)) if target_kind else None
            if target is None and not target_kind:
                matches = [value for key, value in known_targets.items()
                           if key[1] == target_id]
                if len(matches) == 1:
                    target = matches[0]
                    target_kind = target["kind"]
            if observing_team not in (1, 2) or target is None:
                continue
            if (_integer(target.get("team")) == observing_team or
                    _integer(raw.get("target_team"), target.get("team")) != _integer(target.get("team"))):
                continue
            if not bool(target.get("alive", True)):
                continue
            visible = bool(raw.get("visible", True))
            fresh = bool(raw.get("fresh", visible))
            time_left = max(0.0, _number(
                raw.get("time_left"), 10.0 if visible else 0.0))
            contact_key = (target_kind, target_id)
            previous = self._contacts[observing_team].get(contact_key)
            if visible:
                position = _point(raw)
                self._contacts[observing_team][contact_key] = {
                    "id": target_id,
                    "target_kind": target_kind,
                    "team": _integer(target.get("team")),
                    "visible": True,
                    "last_seen": _number(now),
                    "position": position,
                    "health": max(0, _integer(raw.get("health"), 1)),
                    "max_health": max(1, _integer(raw.get("max_health"), 1)),
                    "class_tag": str(raw.get("class_tag") or "unknown")[:24],
                    "armor": max(0.0, _number(raw.get("armor"), 0.0)),
                    "shootable_by_bot_ids": self._bot_id_list(
                        raw.get("shootable_by_bot_ids")),
                }
                accepted += 1
                if accepted_visibility is not None:
                    accepted_visibility.append({
                        "observing_team": observing_team,
                        "target_kind": target_kind,
                        "target_id": target_id,
                        "target_team": _integer(target.get("team")),
                        "visible": True,
                        "fresh": fresh,
                        "time_left": time_left,
                        "visible_by_bot_ids": self._bot_id_list(
                            raw.get("visible_by_bot_ids")),
                        "visible_by_player_ids": self._bot_id_list(
                            raw.get("visible_by_player_ids")),
                        "shootable_by_bot_ids": self._bot_id_list(
                            raw.get("shootable_by_bot_ids")),
                    })
            elif previous is not None:
                previous["visible"] = False
                previous["shootable_by_bot_ids"] = []
                accepted += 1
                if accepted_visibility is not None:
                    accepted_visibility.append({
                        "observing_team": observing_team,
                        "target_kind": target_kind,
                        "target_id": target_id,
                        "target_team": _integer(target.get("team")),
                        "visible": False,
                        "fresh": False,
                        "time_left": 0.0,
                        "visible_by_bot_ids": [],
                        "visible_by_player_ids": [],
                        "shootable_by_bot_ids": [],
                    })
        return accepted

    @staticmethod
    def _bot_id_list(raw):
        if not isinstance(raw, (list, tuple)):
            return []
        result = []
        seen = set()
        for value in raw[:MAX_CONTACTS_PER_TEAM]:
            bot_id = _integer(value)
            if bot_id <= 0 or bot_id in seen:
                continue
            seen.add(bot_id)
            result.append(bot_id)
        return result

    def report_affordances(self, reports, known_bots, known_targets, now):
        """Store client-probed cover geometry after identity validation.

        The server never probes map geometry.  It accepts only candidates for
        a live bot and an enemy already present in that bot team's contact
        memory, then chooses among those candidates globally.
        """
        accepted = 0
        if not isinstance(reports, (list, tuple)):
            return accepted
        for raw in reports[:MAX_COVER_REPORTS]:
            if not isinstance(raw, dict):
                continue
            bot_id = _integer(raw.get("bot_id"))
            bot = known_bots.get(bot_id)
            target_kind = str(raw.get("target_kind") or "")
            target_id = _integer(raw.get("target_id"))
            target_key = (target_kind, target_id)
            target = known_targets.get(target_key)
            if (bot is None or not bot.get("alive", True) or target is None or
                    _integer(bot.get("team")) == _integer(target.get("team"))):
                continue
            contact = self._contacts.get(_integer(bot.get("team")), {}).get(target_key)
            if (contact is None or not contact.get("visible") or
                    _number(now) - _number(contact.get("last_seen")) > CONTACT_TTL_SECONDS):
                continue
            candidates = []
            raw_candidates = raw.get("candidates")
            if not isinstance(raw_candidates, (list, tuple)):
                continue
            bx = _number(bot.get("x"))
            bz = _number(bot.get("z"))
            for value in raw_candidates[:MAX_COVER_CANDIDATES]:
                candidate = normalize_candidate(value)
                if candidate.get("position") is None:
                    continue
                if candidate.get("peek_feasible") and candidate.get("peek_position") is None:
                    continue
                candidate["position"] = _point(candidate.get("position"))
                if candidate.get("peek_position") is not None:
                    candidate["peek_position"] = _point(candidate.get("peek_position"))
                if (candidate["travel_distance"] > 180.0 or
                        candidate["water"] >= 0.5 or candidate["slope"] > 28.0):
                    continue
                if math.hypot(candidate["position"]["x"] - bx,
                              candidate["position"]["z"] - bz) > 180.0:
                    continue
                if (candidate.get("peek_position") is not None and
                        math.hypot(candidate["peek_position"]["x"] - bx,
                                   candidate["peek_position"]["z"] - bz) > 200.0):
                    continue
                candidates.append(candidate)
            if not candidates:
                continue
            self._affordances[bot_id] = {
                "target": target_key,
                "reported_at": _number(now),
                "candidates": candidates,
            }
            accepted += 1
        return accepted

    def build_orders(self, manifest, bot_states, players, now,
                     defense=None):
        known_targets = self.known_targets(bot_states, players)
        contacts = self._prune_contacts(known_targets, now)
        bots = self._alive_bots(manifest, bot_states)
        self._prune_tactical_state(bots, known_targets, now)
        defenders = self._update_base_defense(
            bots, contacts, defense, now)
        self._cover_reservations = set()
        team_axes = dict((team, self._team_base_axis(team, bots))
                         for team in (1, 2))
        capture_targets = dict((team, self._capture_target(defense, team))
                               for team in (1, 2))
        orders = []
        for team in (1, 2):
            team_bots = sorted((bot for bot in bots if bot["team"] == team),
                               key=lambda value: value["id"])
            team_axis = team_axes[team]
            protected_ids = set(defenders.get(team, {}))
            self._rebalance_routes(
                team, team_bots, contacts[team], now, protected_ids)
            assignments = self._assign_targets(team_bots, contacts[team], now)
            assignments = self._prioritize_base_invaders(
                team, team_bots, contacts[team], assignments,
                defenders.get(team, {}), defense)
            for index, bot in enumerate(team_bots):
                order = self._order_for(
                    bot, index, len(team_bots), assignments.get(bot["id"]),
                    contacts[team], now,
                    defenders.get(team, {}).get(bot["id"]), team_axis,
                    team_bots, capture_targets[team],
                    not bool(contacts[team]))
                base = defenders.get(team, {}).get(bot["id"])
                orders.append(order)
        orders.sort(key=lambda value: value["id"])
        payload = {"orders": orders}
        signature_orders = []
        for order in orders:
            signature = dict(order)
            if (signature.get("target_id") is not None and
                    bool(signature.get("fire_allowed"))):
                # The authority client resolves a visible target's rendered live
                # pose by id. Sub-metre contact movement must not create a new
                # 29-order payload and revision on every observation frame.
                signature.pop("aim_position", None)
                signature.pop("face_position", None)
                if signature.get("combat_mode") == "advance_contact":
                    signature.pop("move_position", None)
            signature_orders.append(signature)
        signature_payload = {"orders": signature_orders}
        if signature_payload != self._last_order_signature:
            self.revision += 1
            self._last_order_signature = signature_payload
        self._last_orders = payload
        return {"revision": self.revision, "orders": orders}

    def debug_summary(self, now):
        """Return low-volume evidence for contact -> order diagnostics."""
        result = {"teams": {}}
        orders = ((self._last_orders or {}).get("orders") or [])
        for team in (1, 2):
            known = 0
            visible = 0
            for contact in self._contacts.get(team, {}).values():
                if _number(now) - _number(contact.get("last_seen")) > CONTACT_TTL_SECONDS:
                    continue
                known += 1
                if contact.get("visible"):
                    visible += 1
            team_orders = [order for order in orders
                           if _integer(order.get("team")) == team]
            modes = {}
            for order in team_orders:
                mode = str(order.get("combat_mode") or "unknown")
                modes[mode] = modes.get(mode, 0) + 1
            result["teams"][team] = {
                "contacts": known,
                "visible": visible,
                "orders": len(team_orders),
                "targeted": sum(order.get("target_id") is not None
                                for order in team_orders),
                "fire": sum(bool(order.get("fire_allowed"))
                            for order in team_orders),
                "modes": modes,
            }
        return result

    @staticmethod
    def known_targets(bot_states, players):
        result = {}
        for raw in players or []:
            target_id = _integer(raw.get("id"))
            if target_id:
                result[("human", target_id)] = {
                    "kind": "human", "team": _integer(raw.get("team")),
                    "alive": bool(raw.get("alive", True))}
        for raw in bot_states or []:
            target_id = _integer(raw.get("id"))
            if target_id:
                result[("bot", target_id)] = {
                    "kind": "bot", "team": _integer(raw.get("team")),
                    "alive": bool(raw.get("alive", True))}
        return result

    @staticmethod
    def known_bots(manifest, bot_states):
        states = {_integer(value.get("id")): value for value in (bot_states or [])}
        result = {}
        for raw in manifest or []:
            bot_id = _integer(raw.get("id"))
            if not bot_id:
                continue
            state = states.get(bot_id, {})
            result[bot_id] = {
                "team": _integer(raw.get("team")),
                "alive": bool(state.get("alive", raw.get("health", 1) > 0)),
                "x": _number(state.get("x", raw.get("x"))),
                "z": _number(state.get("z", raw.get("z"))),
            }
        return result

    def clear_observations(self):
        """Discard authority-owned tactical observations after a failover."""
        self._contacts = {1: {}, 2: {}}
        self._affordances = {}
        self._cover_states = {}
        self._cover_reservations = set()
        self._engage_anchors = {}

    def _prune_tactical_state(self, bots, known_targets, now):
        live_bots = dict((bot["id"], bot) for bot in bots)
        for bot_id in list(self._route_states):
            if bot_id not in live_bots:
                del self._route_states[bot_id]
        for bot_id in list(self._route_assignments):
            if bot_id not in live_bots:
                del self._route_assignments[bot_id]
        for bot_id in list(self._target_assignments):
            if bot_id not in live_bots:
                del self._target_assignments[bot_id]
        for bot_id in list(self._engage_anchors):
            if bot_id not in live_bots:
                del self._engage_anchors[bot_id]
        for bot_id in list(self._artillery_anchors):
            if bot_id not in live_bots:
                del self._artillery_anchors[bot_id]
        for bot_id, hit in list(self._recent_hits.items()):
            if (bot_id not in live_bots or not isinstance(hit, dict) or
                    _number(now) - _number(hit.get("reported_at")) >
                    RECENT_HIT_SECONDS):
                del self._recent_hits[bot_id]
        for bot_id, report in list(self._affordances.items()):
            bot = live_bots.get(bot_id)
            target_key = report.get("target") if isinstance(report, dict) else None
            target = known_targets.get(target_key)
            contact = (self._contacts.get(_integer(bot.get("team")), {}).get(target_key)
                       if bot is not None else None)
            if (bot is None or target is None or not target.get("alive") or
                    contact is None or not contact.get("visible") or
                    _number(now) - _number(report.get("reported_at")) > COVER_TTL_SECONDS):
                del self._affordances[bot_id]
        for bot_id, state in list(self._cover_states.items()):
            report = self._affordances.get(bot_id)
            if (bot_id not in live_bots or not isinstance(state, dict) or
                    report is None or state.get("target") != report.get("target")):
                del self._cover_states[bot_id]

    def _prune_contacts(self, known_targets, now):
        result = {1: [], 2: []}
        for team in (1, 2):
            stale = []
            for target_key, contact in self._contacts[team].items():
                target = known_targets.get(target_key)
                if target is None or not target.get("alive") or _number(now) - contact["last_seen"] > CONTACT_TTL_SECONDS:
                    stale.append(target_key)
                else:
                    result[team].append(dict(contact))
            for target_key in stale:
                del self._contacts[team][target_key]
        return result

    @staticmethod
    def _alive_bots(manifest, bot_states):
        states = {_integer(value.get("id")): value for value in (bot_states or [])}
        result = []
        for raw in manifest or []:
            bot_id = _integer(raw.get("id"))
            state = states.get(bot_id, {})
            if not bot_id or not bool(state.get("alive", raw.get("health", 1) > 0)):
                continue
            result.append({
                "id": bot_id,
                "team": _integer(raw.get("team")),
                "slot": _integer(raw.get("slot")),
                "profile": raw.get("profile") if isinstance(raw.get("profile"), dict) else {},
                "route": raw.get("route") if isinstance(raw.get("route"), dict) else {},
                "state": state,
            })
        return result

    @staticmethod
    def _base_defense_eligible(bot):
        state = bot.get("state") if isinstance(bot.get("state"), dict) else {}
        if not bool(state.get("world_pose", True)):
            return False
        critical = state.get("critical")
        critical = critical if isinstance(critical, dict) else {}
        destroyed = set(str(value) for value in
                        (critical.get("destroyed") or ()))
        return not destroyed.intersection((
            "engineHealth", "leftTrackHealth", "rightTrackHealth"))

    @staticmethod
    def _defense_points(raw, team):
        if not isinstance(raw, dict):
            return []
        values = raw.get(str(team), raw.get(team))
        if not isinstance(values, (list, tuple)):
            return []
        result = []
        for index, value in enumerate(values[:4]):
            if not isinstance(value, dict):
                continue
            point = _point(value)
            result.append({
                "id": str(value.get("id") or "%d:%d" % (team, index)),
                "point": point,
            })
        return result

    @staticmethod
    def _defense_eta(bot, point, contacts, deadline):
        state = bot["state"]
        distance = math.hypot(
            point["x"] - _number(state.get("x")),
            point["z"] - _number(state.get("z")))
        speed = _number(bot.get("profile", {}).get("speed"))
        cruise = _clamp(speed * 0.65, 4.0, 22.0)
        eta = 3.0 + distance * 1.30 / cruise
        diversion = 0.0
        for contact in contacts or ():
            if (not contact.get("visible") or
                    bot["id"] not in contact.get(
                        "shootable_by_bot_ids", ())):
                continue
            contact_distance = math.hypot(
                contact["position"]["x"] - _number(state.get("x")),
                contact["position"]["z"] - _number(state.get("z")))
            if contact_distance <= 50.0:
                diversion = max(diversion, 12.0)
            elif contact_distance <= 150.0:
                diversion = max(diversion, 4.0)
        health = max(0.0, _number(state.get("health"), 1.0))
        max_health = max(1.0, _number(state.get("max_health"), 1.0))
        diversion += 6.0 * (1.0 - min(1.0, health / max_health))
        class_tag = str(bot.get("profile", {}).get("class_tag") or "")
        if class_tag == "SPG":
            diversion += 8.0
        elif class_tag == "AT-SPG":
            diversion += 3.0
        if eta <= deadline:
            return (0, eta + diversion, eta, bot["id"])
        return (1, eta - deadline, eta + diversion, bot["id"])

    def _update_base_defense(self, bots, contacts, defense, now):
        """Keep a small, stable responder group while an own base is invaded."""
        if not isinstance(defense, dict):
            self._base_defense = {1: {}, 2: {}}
            return {1: {}, 2: {}}
        defense = defense if isinstance(defense, dict) else {}
        states = defense.get("states")
        states = states if isinstance(states, dict) else {}
        bases = defense.get("bases")
        live_by_team = dict((team, [
            bot for bot in bots
            if bot["team"] == team and self._base_defense_eligible(bot)
        ]) for team in (1, 2))
        result = {1: {}, 2: {}}
        for team in (1, 2):
            incident = self._base_defense.setdefault(team, {})
            responders = incident.setdefault("responders", {})
            live = dict((bot["id"], bot) for bot in live_by_team[team])
            for bot_id in list(responders):
                if bot_id not in live:
                    del responders[bot_id]
            raw_state = states.get(str(team), states.get(team, {}))
            raw_state = raw_state if isinstance(raw_state, dict) else {}
            invaders = max(0, _integer(raw_state.get("invaders")))
            points = self._defense_points(bases, team)
            reserve_limit = (1 if len(live) == 1 else
                             max(0, len(live) - 1))
            if len(responders) > reserve_limit:
                deadline = max(
                    0.0, _number(raw_state.get("time_left")) - 2.0)
                ranked = sorted(
                    responders, key=lambda bot_id: self._defense_eta(
                        live[bot_id], responders[bot_id]["point"],
                        contacts.get(team, ()), deadline))
                keep = set(ranked[:reserve_limit])
                for bot_id in list(responders):
                    if bot_id not in keep:
                        del responders[bot_id]

            if invaders <= 0:
                if not responders:
                    incident["clear_since"] = None
                    incident["need"] = 0
                    continue
                clear_since = incident.get("clear_since")
                if clear_since is None:
                    incident["clear_since"] = _number(now)
                elif _number(now) - _number(clear_since) >= 3.0:
                    responders.clear()
                    incident["clear_since"] = None
                    incident["need"] = 0
                    continue
            else:
                incident["clear_since"] = None
                desired = min(MAX_BASE_DEFENDERS, max(1, invaders))
                if len(live) > 1:
                    desired = min(desired, len(live) - 1)
                elif live:
                    desired = 1
                else:
                    desired = 0
                incident["need"] = max(
                    _integer(incident.get("need")), desired)

                point_by_id = dict((value["id"], value) for value in points)
                for bot_id, record in list(responders.items()):
                    if record.get("base_id") in point_by_id:
                        continue
                    if not points:
                        del responders[bot_id]
                        continue
                    bot = live[bot_id]
                    selected = min(points, key=lambda value: (
                        math.hypot(
                            value["point"]["x"] - _number(
                                bot["state"].get("x")),
                            value["point"]["z"] - _number(
                                bot["state"].get("z"))),
                        value["id"]))
                    responders[bot_id] = {
                        "base_id": selected["id"],
                        "point": dict(selected["point"]),
                    }

                missing = max(0, min(_integer(incident.get("need")),
                                     reserve_limit) - len(responders))
                if missing and points:
                    deadline = max(
                        0.0, _number(raw_state.get("time_left")) - 2.0)
                    candidates = []
                    for bot in live.values():
                        if bot["id"] in responders:
                            continue
                        selected = min(points, key=lambda value: (
                            math.hypot(
                                value["point"]["x"] - _number(
                                    bot["state"].get("x")),
                                value["point"]["z"] - _number(
                                    bot["state"].get("z"))),
                            value["id"]))
                        key = self._defense_eta(
                            bot, selected["point"], contacts.get(team, ()),
                            deadline)
                        candidates.append((key, bot["id"], selected))
                    for unused_key, bot_id, selected in sorted(
                            candidates)[:missing]:
                        responders[bot_id] = {
                            "base_id": selected["id"],
                            "point": dict(selected["point"]),
                        }
            result[team] = dict(responders)
        return result

    def _apply_base_defense_order(self, order, bot, base):
        self._engage_anchors.pop(bot["id"], None)
        self._cover_states.pop(bot["id"], None)
        order["combat_mode"] = "base_defense"
        order["defense_base_id"] = str(base["base_id"])
        order["move_position"] = dict(base["point"])
        order["throttle_override"] = None
        order["route_join"] = False
        if order.get("target_id") is None:
            order["face_position"] = dict(base["point"])

    @staticmethod
    def _capture_target(defense, team):
        """Return the exact opposing CTF circle supplied by BattleState."""
        if not isinstance(defense, dict):
            return None
        bases = defense.get("capture_bases")
        if not isinstance(bases, dict):
            return None
        enemy_team = 3 - int(team)
        values = bases.get(str(enemy_team), bases.get(enemy_team))
        if not isinstance(values, (list, tuple)):
            return None
        for index, raw in enumerate(values):
            if not isinstance(raw, dict):
                continue
            try:
                x = float(raw["x"])
                z = float(raw["z"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(x) or not math.isfinite(z):
                continue
            return {
                "id": str(raw.get("id") or "%d:%d" %
                          (enemy_team, index)),
                "point": _point(raw),
            }
        return None

    def _apply_base_capture_order(self, order, bot, target):
        """Send an unengaged vehicle to the opposing CTF capture circle."""
        self._engage_anchors.pop(bot["id"], None)
        self._cover_states.pop(bot["id"], None)
        point = dict(target["point"])
        order["combat_mode"] = "base_capture"
        order["capture_base_id"] = str(target["id"])
        order["aim_position"] = point
        order["face_position"] = point
        order["move_position"] = point
        order["throttle_override"] = None
        order["route_join"] = False

    def _prioritize_base_invaders(self, team, bots, contacts, assignments,
                                  defenders, defense):
        if not defenders or not isinstance(defense, dict):
            return assignments
        contributor_map = defense.get("contributors")
        contributor_map = (contributor_map
                           if isinstance(contributor_map, dict) else {})
        raw = contributor_map.get(str(team), contributor_map.get(team, ()))
        contributor_keys = set()
        if isinstance(raw, (list, tuple)):
            for value in raw:
                if not isinstance(value, dict):
                    continue
                kind = str(value.get("kind") or "")
                vehicle_id = _integer(value.get("id"))
                if kind in ("human", "bot") and vehicle_id > 0:
                    contributor_keys.add((kind, vehicle_id))
        if not contributor_keys:
            return assignments
        result = dict(assignments)
        bot_by_id = dict((bot["id"], bot) for bot in bots)
        reservations = {}
        for bot_id in sorted(defenders):
            bot = bot_by_id.get(bot_id)
            if bot is None:
                continue
            bx = _number(bot["state"].get("x"))
            bz = _number(bot["state"].get("z"))
            choices = []
            for contact in contacts:
                key = (str(contact.get("target_kind") or ""),
                       _integer(contact.get("id")))
                if (key not in contributor_keys or
                        not contact.get("visible") or
                        bot_id not in contact.get(
                            "shootable_by_bot_ids", ())):
                    continue
                distance = math.hypot(
                    contact["position"]["x"] - bx,
                    contact["position"]["z"] - bz)
                health_fraction = (
                    _number(contact.get("health"), 1.0) /
                    max(1.0, _number(contact.get("max_health"), 1.0)))
                choices.append((
                    reservations.get(key, 0), health_fraction, distance,
                    key[0], key[1], contact))
            if not choices:
                continue
            selected = min(choices)
            contact = selected[-1]
            key = (str(contact.get("target_kind") or ""),
                   _integer(contact.get("id")))
            reservations[key] = reservations.get(key, 0) + 1
            result[bot_id] = contact
            self._target_assignments[bot_id] = {
                "target": key,
                "until": 0.0,
            }
        return result

    @staticmethod
    def _desired_focus(contact):
        remaining = max(0, _integer(contact.get("health")))
        if remaining >= 1800:
            return 3
        if remaining >= 900 or contact.get("class_tag") in ("heavyTank", "AT-SPG"):
            return 2
        return 1

    @staticmethod
    def _focus_limit(contact, distance):
        """Let nearby tanks defend themselves without pulling a whole flank."""
        limit = BotPlanner._desired_focus(contact)
        if distance <= CLOSE_THREAT_DISTANCE:
            return max(limit, CLOSE_THREAT_FOCUS_LIMIT)
        return limit

    @staticmethod
    def _engagement_range(bot, contact):
        """Keep nearby combat primary without pulling an entire team off-route."""
        profile = bot.get("profile") if isinstance(bot.get("profile"), dict) else {}
        roles = profile.get("roles") if isinstance(profile.get("roles"), dict) else {}
        desired = max(40.0, _number(profile.get("desired_range"), 180.0))
        if str(profile.get("class_tag") or "") == "SPG":
            # The authority client puts an SPG id in shootable_by_bot_ids only
            # after a pitch-valid, obstacle-free ballistic path is complete.
            # Do not discard that stronger proof through the old 560 m direct
            # fire envelope.
            return max(
                desired,
                min(2500.0, _number(profile.get("fire_range"), 1250.0)))
        mobility = max(_number(roles.get("scout")),
                       _number(roles.get("flanker")))
        if contact.get("visible"):
            distance = max(340.0, min(560.0,
                                     desired * 2.0 + mobility * 300.0))
        else:
            distance = max(240.0, min(420.0,
                                     desired * 1.5 + mobility * 210.0))
        return distance

    def _recent_hit(self, bot_id, now):
        hit = self._recent_hits.get(_integer(bot_id))
        if not isinstance(hit, dict):
            return None
        if (_number(now) - _number(hit.get("reported_at")) >
                RECENT_HIT_SECONDS):
            self._recent_hits.pop(_integer(bot_id), None)
            return None
        return hit

    def _recent_threat_contact(self, bot, contacts, now):
        hit = self._recent_hit(bot["id"], now)
        if hit is None:
            return None
        key = hit.get("attacker")
        for contact in contacts:
            if (str(contact.get("target_kind") or ""),
                    _integer(contact.get("id"))) == key:
                return contact
        return None

    @staticmethod
    def _ally_support_score(bot, team_bots, focus):
        """Return nearby and forward ally support on a normalized scale."""
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        tx = _number(focus.get("position", {}).get("x"))
        tz = _number(focus.get("position", {}).get("z"))
        score = 0.0
        for ally in team_bots or ():
            if ally["id"] == bot["id"]:
                continue
            state = ally.get("state") if isinstance(
                ally.get("state"), dict) else {}
            health_fraction = (_number(state.get("health"), 1.0) /
                               max(1.0, _number(
                                   state.get("max_health"), 1.0)))
            bot_distance = math.hypot(
                _number(state.get("x")) - bx,
                _number(state.get("z")) - bz)
            target_distance = math.hypot(
                _number(state.get("x")) - tx,
                _number(state.get("z")) - tz)
            score += health_fraction * max(
                0.0, 1.0 - bot_distance / 130.0) * 0.55
            score += health_fraction * max(
                0.0, 1.0 - target_distance / 220.0) * 0.35
        return round(_clamp(score, 0.0, 1.0), 3)

    @staticmethod
    def _crossfire_risk(bot, contacts):
        """Estimate multi-direction exposure from current visible headings."""
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        bearings = []
        for contact in contacts:
            if (not contact.get("visible") or
                    bot["id"] not in contact.get(
                        "shootable_by_bot_ids", ())):
                continue
            dx = _number(contact.get("position", {}).get("x")) - bx
            dz = _number(contact.get("position", {}).get("z")) - bz
            distance = math.hypot(dx, dz)
            if distance <= 1.0 or distance > CROSSFIRE_MAX_DISTANCE:
                continue
            bearings.append(math.atan2(dx, dz))
        best = 0.0
        for first_index, first in enumerate(bearings):
            for second in bearings[first_index + 1:]:
                separation = abs((first - second + math.pi) %
                                 (math.pi * 2.0) - math.pi)
                if separation < CROSSFIRE_MIN_ANGLE:
                    continue
                best = max(best, _clamp(
                    (separation - CROSSFIRE_MIN_ANGLE) /
                    math.radians(90.0), 0.0, 1.0))
        return round(best, 3)

    def _assign_targets(self, bots, contacts, now):
        """Assign only locally shootable contacts, with a hard focus cap.

        Team spotting is shared intelligence, not proof that every tank has a
        firing lane. The authority client reports the bot ids whose own static
        ray succeeded, so a shared red dot cannot pull the rest of a flank off
        its route.
        """
        if not bots or not contacts:
            return {}
        reservations = {}
        assigned = {}
        candidates = []
        by_bot = {}
        for bot in bots:
            bx = _number(bot["state"].get("x"))
            bz = _number(bot["state"].get("z"))
            for contact in contacts:
                if (not contact.get("visible") or
                        bot["id"] not in contact.get(
                            "shootable_by_bot_ids", ())):
                    continue
                distance = math.hypot(
                    contact["position"]["x"] - bx,
                    contact["position"]["z"] - bz)
                if distance > self._engagement_range(bot, contact):
                    continue
                score = 0.0
                score += contact["health"] / float(max(1, contact["max_health"])) * 28.0
                score += distance * 0.018
                hit = self._recent_hit(bot["id"], now)
                if (hit is not None and
                        hit.get("attacker") == (
                            str(contact.get("target_kind") or ""),
                            _integer(contact.get("id")))):
                    score -= RECENT_ATTACKER_SCORE_BONUS
                if distance <= CLOSE_THREAT_DISTANCE:
                    # A point-blank enemy is an immediate self-defence problem,
                    # even while a previous long-range target still owns a
                    # short lease.
                    score -= CLOSE_THREAT_SCORE_BONUS
                sort_key = (
                    score, bot["id"], str(contact.get("target_kind") or ""),
                    contact["id"], distance)
                candidate = (sort_key, bot, contact, distance)
                candidates.append(candidate)
                by_bot.setdefault(bot["id"], []).append(candidate)

        # Preserve a valid target through small score changes. Without this,
        # sub-metre motion between two similar enemies flips the order every
        # server tick and repeatedly cancels the client's private A* search.
        for bot in bots:
            bot_candidates = sorted(
                by_bot.get(bot["id"], ()), key=lambda value: value[0])
            if not bot_candidates:
                self._target_assignments.pop(bot["id"], None)
                continue
            previous = self._target_assignments.get(bot["id"])
            if not isinstance(previous, dict):
                continue
            previous_candidate = None
            for candidate in bot_candidates:
                contact = candidate[2]
                key = (contact.get("target_kind"), contact["id"])
                if key == previous.get("target"):
                    previous_candidate = candidate
                    break
            if previous_candidate is None:
                self._target_assignments.pop(bot["id"], None)
                continue
            best_score = bot_candidates[0][0][0]
            lease_expired = _number(now) >= _number(previous.get("until"))
            best_candidate = bot_candidates[0]
            best_key = (best_candidate[2].get("target_kind"),
                        best_candidate[2]["id"])
            close_override = (
                best_key != previous.get("target") and
                best_candidate[3] <= CLOSE_THREAT_DISTANCE and
                previous_candidate[3] > CLOSE_THREAT_DISTANCE
            )
            hit = self._recent_hit(bot["id"], now)
            attacker_override = bool(
                hit is not None and best_key == hit.get("attacker") and
                best_key != previous.get("target"))
            if close_override or attacker_override:
                continue
            if (lease_expired and
                    previous_candidate[0][0] >
                    best_score + TARGET_SWITCH_MARGIN):
                continue
            contact = previous_candidate[2]
            key = (contact.get("target_kind"), contact["id"])
            if reservations.get(key, 0) >= self._focus_limit(
                    contact, previous_candidate[3]):
                continue
            reservations[key] = reservations.get(key, 0) + 1
            assigned[bot["id"]] = contact
            if lease_expired:
                previous["until"] = _number(now) + TARGET_LEASE_SECONDS
        for (unused_sort_key, bot, contact, distance) in sorted(
                candidates, key=lambda value: value[0]):
            if bot["id"] in assigned:
                continue
            key = (contact.get("target_kind"), contact["id"])
            if reservations.get(key, 0) >= self._focus_limit(
                    contact, distance):
                continue
            reservations[key] = reservations.get(key, 0) + 1
            assigned[bot["id"]] = contact
            self._target_assignments[bot["id"]] = {
                "target": key,
                "until": _number(now) + TARGET_LEASE_SECONDS,
            }
        for bot in bots:
            if bot["id"] not in assigned:
                self._target_assignments.pop(bot["id"], None)
        return assigned

    @staticmethod
    def _route_catalog(bots):
        result = {}
        for bot in bots:
            route = bot.get("route") if isinstance(bot.get("route"), dict) else {}
            route_id = str(route.get("id") or "")
            if route_id and route.get("waypoints") and route_id not in result:
                result[route_id] = route
        return result

    @staticmethod
    def _team_base_axis(team, bots):
        """Infer one direction-neutral own/enemy axis from uploaded routes.

        Baked routes start at their team's own base, so the opposing team's
        route starts provide the cleanest enemy-base endpoint. If no opposing
        bot exists, the farthest own-route endpoint is the conservative fallback
        because a local rear-guard route may end near its own base. The
        calculation is invariant under map rotation and reflection.
        """
        starts = {1: [], 2: []}
        own_ends = []
        for bot in bots:
            route = bot.get("route") if isinstance(bot.get("route"), dict) else {}
            waypoints = route.get("waypoints")
            if not isinstance(waypoints, list) or len(waypoints) < 2:
                continue
            bot_team = _integer(bot.get("team"))
            if bot_team not in (1, 2):
                continue
            starts[bot_team].append(_point(waypoints[0]))
            if bot_team == team:
                own_ends.append(_point(waypoints[-1]))
        own_starts = starts.get(team, ())
        if not own_starts or not own_ends:
            return None
        own = {
            "x": round(sum(value["x"] for value in own_starts) /
                       len(own_starts), 3),
            "y": round(sum(value["y"] for value in own_starts) /
                       len(own_starts), 3),
            "z": round(sum(value["z"] for value in own_starts) /
                       len(own_starts), 3),
        }
        enemy_starts = starts.get(3 - team, ())
        if enemy_starts:
            enemy_ends = enemy_starts
        else:
            distances = [math.hypot(value["x"] - own["x"],
                                    value["z"] - own["z"])
                         for value in own_ends]
            farthest = max(distances)
            # Average equally distant through-route endpoints instead of
            # breaking a geometric tie with ids or world-axis signs.
            threshold = max(1.0, farthest * 0.02)
            enemy_ends = [value for value, distance in
                          zip(own_ends, distances)
                          if farthest - distance <= threshold]
        enemy = {
            "x": round(sum(value["x"] for value in enemy_ends) /
                       len(enemy_ends), 3),
            "y": round(sum(value["y"] for value in enemy_ends) /
                       len(enemy_ends), 3),
            "z": round(sum(value["z"] for value in enemy_ends) /
                       len(enemy_ends), 3),
        }
        if math.hypot(enemy["x"] - own["x"],
                      enemy["z"] - own["z"]) < 1.0:
            return None
        return own, enemy

    def _artillery_anchor(self, bot, team_axis):
        """Choose a stable rear staging point from this SPG's safe route.

        The server has graph-validated macro points but no static visibility or
        shell-arc probe. Select only by distance from the own base and progress
        on the own/enemy axis; ``hold`` annotations are deliberately not treated
        as proof that a point is an artillery position.
        """
        route = bot.get("route") if isinstance(bot.get("route"), dict) else {}
        waypoints = route.get("waypoints")
        route_signature = (
            str(route.get("id") or ""),
            tuple((_number(value.get("x")), _number(value.get("y")),
                   _number(value.get("z")))
                  for value in waypoints)
            if isinstance(waypoints, list) else (),
        )
        cached = self._artillery_anchors.get(bot["id"])
        if (isinstance(cached, dict) and
                cached.get("route_signature") == route_signature):
            return {
                "point": dict(cached["point"]),
                "face": dict(cached["face"]),
                "index": cached["index"],
            }
        if not isinstance(waypoints, list) or not waypoints:
            position = _point(bot.get("state"))
            result = {
                "point": position,
                "face": position,
                "index": 0,
            }
            self._artillery_anchors[bot["id"]] = dict(
                result, route_signature=route_signature)
            return result
        points = [_point(value) for value in waypoints]
        if team_axis is None:
            own = points[0]
            enemy = points[-1]
        else:
            own, enemy = team_axis
        axis_x = enemy["x"] - own["x"]
        axis_z = enemy["z"] - own["z"]
        axis_length = math.hypot(axis_x, axis_z)
        if axis_length < 1.0:
            own = points[0]
            enemy = points[-1]
            axis_x = enemy["x"] - own["x"]
            axis_z = enemy["z"] - own["z"]
            axis_length = max(1.0, math.hypot(axis_x, axis_z))
        axis_squared = axis_length * axis_length
        desired_radius = _clamp(axis_length * 0.16, 35.0, 120.0)
        candidates = []
        for point_index, point in enumerate(points):
            offset_x = point["x"] - own["x"]
            offset_z = point["z"] - own["z"]
            radius = math.hypot(offset_x, offset_z)
            progress = (offset_x * axis_x + offset_z * axis_z) / axis_squared
            outside_rear = max(0.0, progress - 0.30,
                               -0.12 - progress)
            base_overlap = max(0.0, desired_radius * 0.35 - radius)
            score = (outside_rear * axis_length * 8.0 +
                     base_overlap * 5.0 +
                     abs(radius - desired_radius) +
                     abs(progress - 0.12) * axis_length * 0.20)
            candidates.append((score, point_index, point))
        unused_score, point_index, point = min(candidates)
        result = {
            "point": dict(point),
            "face": dict(enemy),
            "index": point_index,
        }
        self._artillery_anchors[bot["id"]] = dict(
            result, route_signature=route_signature)
        return result

    @staticmethod
    def _nearest_route(contact, catalog):
        best = None
        best_key = None
        point = contact.get("position") or {}
        for route_id, route in sorted(catalog.items()):
            distance = None
            for waypoint in route.get("waypoints") or []:
                dx = _number(waypoint.get("x")) - _number(point.get("x"))
                dz = _number(waypoint.get("z")) - _number(point.get("z"))
                value = dx * dx + dz * dz
                if distance is None or value < distance:
                    distance = value
            key = (distance if distance is not None else 1e18, route_id)
            if best_key is None or key < best_key:
                best_key = key
                best = route_id
        return best

    def _rebalance_routes(self, team, bots, contacts, now,
                          protected_ids=()):
        """Move at most one adaptable tank toward a pressured route every 4s."""
        catalog = self._route_catalog(bots)
        for bot in bots:
            route = bot.get("route") if isinstance(bot.get("route"), dict) else {}
            route_id = str(route.get("id") or "")
            assigned = self._route_assignments.get(bot["id"])
            assigned_route = assigned.get("route") if isinstance(assigned, dict) else None
            assigned_id = (str(assigned_route.get("id") or "")
                           if isinstance(assigned_route, dict) else "")
            assigned_until = _number(assigned.get("until")) if isinstance(assigned, dict) else 0.0
            if (assigned_id not in catalog or
                    assigned_route != catalog.get(assigned_id) or
                    (assigned_until > 0.0 and assigned_until <= _number(now))):
                if route_id in catalog:
                    self._route_assignments[bot["id"]] = {
                        "route": catalog[route_id], "until": 0.0,
                    }
                else:
                    self._route_assignments.pop(bot["id"], None)
                self._route_states.pop(bot["id"], None)
        if len(catalog) < 2:
            return
        if _number(now) < _number(self._next_route_rebalance.get(team)):
            return
        self._next_route_rebalance[team] = _number(now) + ROUTE_REBALANCE_SECONDS
        pressure = dict((route_id, 0.0) for route_id in catalog)
        for contact in contacts:
            route_id = self._nearest_route(contact, catalog)
            if route_id is None:
                continue
            health_fraction = (_number(contact.get("health"), 1.0) /
                               max(1.0, _number(contact.get("max_health"), 1.0)))
            pressure[route_id] += max(0.3, health_fraction)
        if not pressure or max(pressure.values()) <= 0.0:
            return
        counts = dict((route_id, 0) for route_id in catalog)
        for bot in bots:
            assignment = self._route_assignments.get(bot["id"], {})
            route = assignment.get("route") if isinstance(assignment, dict) else None
            if not isinstance(route, dict):
                route = bot.get("route") or {}
            route_id = str(route.get("id") or "")
            if route_id in counts:
                counts[route_id] += 1
        target_route = max(sorted(catalog), key=lambda route_id:
                           pressure[route_id] - counts[route_id] * 0.45)
        if pressure[target_route] - counts[target_route] * 0.45 <= 0.0:
            return
        # Re-evaluation happens before a temporary assignment expires. Renew
        # the same pressured route in place so its waypoint index survives;
        # only a real route change clears progress below.
        for bot in bots:
            assignment = self._route_assignments.get(bot["id"])
            route = assignment.get("route") if isinstance(assignment, dict) else None
            if (isinstance(route, dict) and
                    str(route.get("id") or "") == target_route and
                    _number(assignment.get("until")) > 0.0):
                assignment["until"] = _number(now) + ROUTE_LEASE_SECONDS
        candidates = []
        for bot in bots:
            if bot["id"] in protected_ids:
                continue
            if str(bot.get("profile", {}).get("class_tag") or "") == "SPG":
                continue
            assignment = self._route_assignments.get(bot["id"], {})
            current = assignment.get("route") if isinstance(assignment, dict) else None
            if not isinstance(current, dict):
                current = bot.get("route") or {}
            current_id = str(current.get("id") or "")
            if current_id == target_route or counts.get(current_id, 0) <= 1:
                continue
            roles = bot.get("profile", {}).get("roles") or {}
            mobility = max(_number(roles.get("support")),
                           _number(roles.get("flanker")),
                           _number(roles.get("scout")))
            personality = self._personality(bot["id"])
            score = (mobility * 2.0 + personality["adaptability"] -
                     _number(roles.get("brawler")) * 0.65 -
                     pressure.get(current_id, 0.0) * 0.7)
            candidates.append((score, -bot["id"], bot))
        if not candidates:
            return
        donor = max(candidates)[2]
        self._route_assignments[donor["id"]] = {
            "route": catalog[target_route],
            "until": _number(now) + ROUTE_LEASE_SECONDS,
        }
        self._route_states.pop(donor["id"], None)

    def _route(self, bot, now):
        assignment = self._route_assignments.get(bot["id"])
        route = assignment.get("route") if isinstance(assignment, dict) else None
        if not isinstance(route, dict):
            route = bot.get("route") if isinstance(bot.get("route"), dict) else {}
        waypoints = route.get("waypoints") if isinstance(route.get("waypoints"), list) else []
        if not waypoints:
            route_ids = ("left_flank", "center_line", "right_flank")
            route_id = route_ids[(bot["slot"] + bot["id"]) % len(route_ids)]
            side = -1.0 if route_id == "left_flank" else (1.0 if route_id == "right_flank" else 0.0)
            direction = 1.0 if bot["team"] == 1 else -1.0
            point = {"x": round(side * 115.0, 3), "y": 0.0,
                     "z": round(direction * 18.0, 3)}
            return route_id, 0, point, point, False
        route_id = str(route.get("id") or "uploaded_route")
        state = self._route_states.get(bot["id"])
        if state is None or state.get("route_id") != route_id:
            bx = _number(bot["state"].get("x"))
            bz = _number(bot["state"].get("z"))
            nearest = min(range(len(waypoints)), key=lambda value:
                          (_number(waypoints[value].get("x")) - bx) ** 2 +
                          (_number(waypoints[value].get("z")) - bz) ** 2)
            index = nearest
            # Route point zero is the team's own flag. The actual line-up starts
            # in front of it, so do not order every deployed tank to turn around
            # and visit its own base before it may advance toward the enemy flag.
            if nearest == 0 and len(waypoints) > 1:
                index = 1
            while (index + 1 < len(waypoints) and
                   math.hypot(_number(waypoints[index].get("x")) - bx,
                              _number(waypoints[index].get("z")) - bz) < 30.0):
                index += 1
            # Bot snapshots carry hull yaw in the same shared frame as uploaded
            # routes. Skip only a truly rear-facing connector; a side road remains a
            # valid lane opening and must not be flattened into the next macro point.
            yaw = _number(bot["state"].get("yaw"))
            while index + 1 < len(waypoints):
                point = waypoints[index]
                bearing = math.atan2(_number(point.get("x")) - bx,
                                     _number(point.get("z")) - bz)
                delta = (bearing - yaw + math.pi) % (math.pi * 2.0) - math.pi
                if abs(delta) <= 1.75:
                    break
                index += 1
            state = {"index": index, "route_id": route_id,
                     "join_index": index,
                     "join_anchor": {"x": bx,
                                     "y": _number(bot["state"].get("y")),
                                     "z": bz}}
            self._route_states[bot["id"]] = state
        index = min(max(0, _integer(state.get("index"))), len(waypoints) - 1)
        point = _point(waypoints[index])
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        reached = math.hypot(point["x"] - bx, point["z"] - bz) <= 13.0
        # Macro points describe the lane, not parking places.  Tanks keep
        # advancing until combat, safety or the final destination stops them.
        if reached and index + 1 < len(waypoints):
            index += 1
            state["index"] = index
            point = _point(waypoints[index])
        route_join = (state.get("join_index") == index and
                      isinstance(state.get("join_anchor"), dict))
        if route_join:
            anchor = _point(state["join_anchor"])
        else:
            anchor = _point(waypoints[max(0, index - 1)])
        return route_id, index, point, anchor, route_join

    def _retreat_point(self, bot, route_anchor):
        """Return the previous graph-validated route point when available."""
        assignment = self._route_assignments.get(bot["id"])
        route = assignment.get("route") if isinstance(
            assignment, dict) else None
        if not isinstance(route, dict):
            route = bot.get("route") if isinstance(bot.get("route"), dict) else {}
        waypoints = route.get("waypoints")
        route_state = self._route_states.get(bot["id"])
        if (not isinstance(waypoints, list) or not waypoints or
                not isinstance(route_state, dict)):
            return dict(route_anchor)
        index = min(max(0, _integer(route_state.get("index"))),
                    len(waypoints) - 1)
        return _point(waypoints[max(0, index - 1)])

    @staticmethod
    def _flank_point(bot, contact, desired_range):
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        tx = contact["position"]["x"]
        tz = contact["position"]["z"]
        dx, dz = bx - tx, bz - tz
        length = math.hypot(dx, dz) or 1.0
        dx, dz = dx / length, dz / length
        side = -1.0 if bot["id"] % 2 else 1.0
        return _point({
            "x": tx + dx * desired_range * 0.72 + dz * min(95.0, desired_range * 0.38) * side,
            "y": contact["position"].get("y", 0.0),
            "z": tz + dz * desired_range * 0.72 - dx * min(95.0, desired_range * 0.38) * side,
        })

    @staticmethod
    def _shell_index(profile, contact, personality, state=None):
        """Plan the next round: standard first, HE soft, premium hard.

        Descriptor order supplies the standard baseline. A later non-HE round
        is treated as premium only when it has materially higher penetration;
        store prices are not part of the stable LAN profile. Depleted rounds
        are never requested.
        """
        shells = profile.get("shells") if isinstance(profile.get("shells"), list) else []
        if not shells:
            return 0
        remaining = ((state or {}).get("ammo_remaining")
                     if isinstance(state, dict) else None)
        if not isinstance(remaining, (list, tuple)):
            remaining = None
        available = []
        for shell in shells:
            index = max(0, _integer(shell.get("index")))
            if (remaining is not None and
                    (index >= len(remaining) or
                     _integer(remaining[index]) <= 0)):
                continue
            available.append(shell)
        if not available:
            return max(0, _integer((state or {}).get("shell_index", 0)))
        armor = max(0.0, _number(contact.get("armor")))
        health = max(0.0, _number(contact.get("health")))
        non_he = []
        high_explosive = []
        for shell in available:
            kind = str(shell.get("kind") or "").lower()
            is_he = ("high_explosive" in kind or
                     ("explosive" in kind and
                      "armor_piercing" not in kind))
            (high_explosive if is_he else non_he).append(shell)
        baseline = min(
            non_he, key=lambda shell: _integer(shell.get("index"))) \
            if non_he else None
        baseline_penetration = max(
            0.0, _number((baseline or {}).get("penetration")))
        standard = []
        premium = []
        for shell in non_he:
            penetration = max(0.0, _number(shell.get("penetration")))
            if (shell is not baseline and baseline_penetration > 0.0 and
                    penetration >= baseline_penetration * 1.03):
                premium.append(shell)
            else:
                standard.append(shell)

        def best_penetration(values):
            return max(values, key=lambda shell: (
                _number(shell.get("penetration")),
                _number(shell.get("damage")),
                -_integer(shell.get("index")))) if values else None

        normal = best_penetration(standard)
        gold = best_penetration(premium)
        explosive = max(high_explosive, key=lambda shell: (
            _number(shell.get("damage")),
            _number(shell.get("penetration")),
            -_integer(shell.get("index")))) if high_explosive else None
        if armor <= 0.0:
            if normal is not None:
                return max(0, _integer(normal.get("index")))
            if gold is not None:
                return max(0, _integer(gold.get("index")))
            return max(0, _integer(explosive.get("index"))) \
                if explosive is not None else 0
        if explosive is not None:
            he_penetration = max(
                0.0, _number(explosive.get("penetration")))
            he_damage = max(0.0, _number(explosive.get("damage")))
            fragile = armor <= he_penetration * 0.90
            finisher = (health > 0.0 and
                        health <= he_damage * (
                            0.72 + personality["aggression"] * 0.18) and
                        armor <= he_penetration * 1.10)
            if fragile or finisher:
                return max(0, _integer(explosive.get("index")))
        if normal is not None:
            normal_penetration = max(
                0.0, _number(normal.get("penetration")))
            if gold is not None and normal_penetration < armor * 1.05:
                return max(0, _integer(gold.get("index")))
            return max(0, _integer(normal.get("index")))
        if gold is not None:
            return max(0, _integer(gold.get("index")))
        if explosive is not None:
            return max(0, _integer(explosive.get("index")))
        return max(0, _integer(available[0].get("index")))

    def _cover_candidate(self, bot, focus, personality, now, urgent=False):
        report = self._affordances.get(bot["id"])
        target_key = (focus.get("target_kind"), focus["id"])
        if (report is None or report.get("target") != target_key or
                _number(now) - _number(report.get("reported_at")) > COVER_TTL_SECONDS):
            self._cover_states.pop(bot["id"], None)
            return None
        weights = {
            "enemy_occlusion": 26.0 + personality["caution"] * 18.0,
            "travel_distance": -0.035 - personality["caution"] * 0.035,
            "escape_feasible": 8.0 + personality["patience"] * 10.0,
            "peek_feasible": 6.0 + personality["aggression"] * 8.0,
        }
        if urgent:
            # A recently hit tank values nearby hard occlusion over a longer,
            # prettier firing position.  The client still proves every point.
            weights["enemy_occlusion"] += 22.0
            weights["exposure"] = -48.0
            weights["travel_distance"] -= 0.045
            weights["escape_feasible"] += 10.0
        ranked = score_candidates(report.get("candidates"), weights)
        usable = [candidate for candidate in ranked
                  if candidate["water"] < 0.5 and candidate["slope"] <= 24.0 and
                  candidate["enemy_occlusion"] >= 0.45 and
                  candidate["peek_feasible"] and candidate["escape_feasible"] and
                  candidate.get("peek_position") is not None]
        if not usable:
            self._cover_states.pop(bot["id"], None)
            return None
        current = self._cover_states.get(bot["id"])
        selected = None
        if (current is not None and current.get("target") == target_key and
                not current.pop("refresh_candidate", False)):
            current_id = current.get("candidate_id")
            for candidate in usable:
                if candidate.get("id") == current_id:
                    selected = candidate
                    current["candidate"] = candidate
                    break
        if selected is None:
            for candidate in usable:
                point = candidate["position"]
                reservation = (int(round(point["x"] / 8.0)),
                               int(round(point["z"] / 8.0)))
                if reservation not in self._cover_reservations:
                    selected = candidate
                    break
            if selected is None:
                selected = usable[0]
            current = {
                "target": target_key,
                "candidate_id": selected["id"],
                "candidate": selected,
                "phase": "approach",
                "phase_until": 0.0,
            }
            self._cover_states[bot["id"]] = current
        point = selected["position"]
        self._cover_reservations.add((int(round(point["x"] / 8.0)),
                                      int(round(point["z"] / 8.0))))
        return selected, current

    def _apply_cover_order(self, order, bot, focus, personality, now,
                           urgent=False, hold_only=False):
        selected_state = self._cover_candidate(
            bot, focus, personality, now, urgent=urgent or hold_only)
        if selected_state is None:
            return False
        candidate, state = selected_state
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        cover = candidate["position"]
        peek = candidate["peek_position"]
        cover_distance = math.hypot(cover["x"] - bx, cover["z"] - bz)
        peek_distance = math.hypot(peek["x"] - bx, peek["z"] - bz)
        phase = state.get("phase", "approach")
        if (urgent or hold_only) and phase == "peek":
            phase = "return"
            state["phase"] = phase
            state["phase_until"] = 0.0
        if phase in ("approach", "return") and cover_distance <= 4.5:
            completed_return = phase == "return"
            phase = "hold"
            state["phase"] = phase
            if completed_return:
                state["refresh_candidate"] = True
            state["phase_until"] = (_number(now) + 0.65 +
                                    personality["patience"] * 1.35)
        elif (phase == "hold" and not urgent and not hold_only and
              _number(now) >= _number(state.get("phase_until"))):
            phase = "peek"
            state["phase"] = phase
            state["phase_until"] = 0.0
        elif phase == "peek" and peek_distance <= 4.5:
            if _number(state.get("phase_until")) <= 0.0:
                state["phase_until"] = (_number(now) + 1.0 +
                                        personality["aggression"] * 1.8)
            elif _number(now) >= _number(state.get("phase_until")):
                phase = "return"
                state["phase"] = phase
                state["phase_until"] = 0.0
        order["cover_id"] = candidate["id"]
        # This flag is permission to shoot, not a claim that the current lane
        # is clear.  The authority client still performs the final per-bot LOS,
        # turret alignment and reload checks.  Keeping it enabled lets a bot
        # engage an exposed target while approaching, holding or leaving cover.
        can_fire = bool(order.get("fire_allowed"))
        if phase == "approach":
            order["combat_mode"] = "take_cover"
            order["move_position"] = dict(cover)
            order["throttle_override"] = 0.72
        elif phase == "hold":
            order["combat_mode"] = "cover_hold"
            order["move_position"] = dict(cover)
            order["throttle_override"] = 0.0
        elif phase == "peek":
            order["combat_mode"] = "cover_peek"
            order["move_position"] = dict(peek)
            order["throttle_override"] = 0.56 if peek_distance > 4.5 else 0.0
            order["fire_allowed"] = can_fire
        else:
            order["combat_mode"] = "cover_return"
            order["move_position"] = dict(cover)
            order["throttle_override"] = None
        return True

    def _apply_artillery_order(self, order, bot, team_axis):
        self._engage_anchors.pop(bot["id"], None)
        self._cover_states.pop(bot["id"], None)
        anchor = self._artillery_anchor(bot, team_axis)
        point = anchor["point"]
        state = bot.get("state") if isinstance(bot.get("state"), dict) else {}
        distance = math.hypot(point["x"] - _number(state.get("x")),
                              point["z"] - _number(state.get("z")))
        arrived = distance <= 15.0
        order["combat_mode"] = (
            "artillery_hold" if arrived else "artillery_deploy")
        order["move_position"] = dict(point)
        order["route_index"] = anchor["index"]
        order["route_anchor"] = dict(point)
        order["route_join"] = False
        order["throttle_override"] = 0.0 if arrived else None
        if order.get("target_id") is None:
            order["face_position"] = dict(anchor["face"])

    @staticmethod
    def _set_target(order, bot, contact, profile, personality,
                    fire_allowed):
        order["target_id"] = contact["id"]
        order["target_kind"] = contact.get("target_kind")
        order["aim_position"] = dict(contact["position"])
        order["face_position"] = dict(contact["position"])
        order["fire_allowed"] = bool(fire_allowed)
        order["shell_index"] = BotPlanner._shell_index(
            profile, contact, personality, bot.get("state"))

    @staticmethod
    def _angled_face_point(bot, target, personality):
        """Return a 12-30 degree hull face point for a stationary turret."""
        bx = _number(bot["state"].get("x"))
        by = _number(bot["state"].get("y"))
        bz = _number(bot["state"].get("z"))
        dx = _number(target.get("x")) - bx
        dz = _number(target.get("z")) - bz
        if math.hypot(dx, dz) <= 0.1:
            return _point(target), 0.0
        degrees = 12.0 + personality["caution"] * 18.0
        if bot["id"] % 2:
            degrees = -degrees
        radians = math.radians(degrees)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        return _point({
            "x": bx + dx * cosine - dz * sine,
            "y": by,
            "z": bz + dx * sine + dz * cosine,
        }), round(degrees, 3)

    @staticmethod
    def _may_angle_hull(profile):
        roles = profile.get("roles") if isinstance(
            profile.get("roles"), dict) else {}
        return bool(
            str(profile.get("class_tag") or "") in (
                "heavyTank", "mediumTank", "lightTank") and
            (_number(profile.get("armor")) >= 60.0 or
             _number(roles.get("brawler")) >= 0.55))

    def _apply_stationary_angling(self, order, bot, profile, personality):
        # Angling is intentionally limited to a stopped firing hold.  Movement,
        # cover peeks, withdrawals and limited-traverse TDs keep their proven
        # travel/weapon heading instead of rotating into nearby geometry.
        if (order.get("combat_mode") != "engage" or
                _number(order.get("throttle_override"), 1.0) != 0.0 or
                not order.get("fire_allowed") or
                not self._may_angle_hull(profile)):
            return
        point, degrees = self._angled_face_point(
            bot, order.get("aim_position") or {}, personality)
        if abs(degrees) <= 0.01:
            return
        order["face_position"] = point
        order["hull_angle_degrees"] = degrees

    def _order_for(self, bot, index, count, focus, contacts, now,
                   travel_override=None, team_axis=None, team_bots=None,
                   capture_target=None, no_known_enemies=False):
        route_id, route_index, move, route_anchor, route_join = self._route(bot, now)
        retreat_point = self._retreat_point(bot, route_anchor)
        profile = dict(bot["profile"])
        desired_range = max(10.0, _number(profile.get("desired_range"), 180.0))
        fire_range = max(desired_range, _number(profile.get("fire_range"), 500.0))
        personality = self._personality(bot["id"])
        state = bot.get("state") if isinstance(bot.get("state"), dict) else {}
        health_fraction = (_number(state.get("health"), 1.0) /
                           max(1.0, _number(state.get("max_health"), 1.0)))
        low_health = health_fraction <= (
            LOW_HEALTH_BASE_FRACTION + personality["caution"] * 0.18)
        recent_hit = self._recent_hit(bot["id"], now)
        threat_contact = self._recent_threat_contact(bot, contacts, now)
        order = {
            "id": bot["id"],
            "team": bot["team"],
            "target_id": None,
            "aim_position": None,
            "face_position": None,
            "move_position": move,
            "fire_allowed": False,
            "combat_mode": "route",
            # The local driver owns safety and steering.  A normal route must
            # not replace its full throttle with a server-side cruise limit.
            "throttle_override": None,
            "desired_range": round(desired_range, 3),
            "fire_range": round(fire_range, 3),
            "route_id": route_id,
            "route_index": route_index,
            "route_anchor": dict(route_anchor),
            "route_join": bool(route_join),
            "personality": personality,
            "profile": profile,
            "shell_index": 0,
        }
        if travel_override is not None:
            if focus is not None:
                observers = focus.get("shootable_by_bot_ids")
                self._set_target(
                    order, bot, focus, profile, personality,
                    bool(focus.get("visible") and
                         bot["id"] in (observers or ())))
            self._apply_base_defense_order(order, bot, travel_override)
            return order
        if no_known_enemies and capture_target is not None:
            self._apply_base_capture_order(order, bot, capture_target)
            return order
        if str(profile.get("class_tag") or "") == "SPG":
            if focus is not None:
                observers = focus.get("shootable_by_bot_ids")
                self._set_target(
                    order, bot, focus, profile, personality,
                    bool(focus.get("visible") and
                         bot["id"] in (observers or ())))
            self._apply_artillery_order(order, bot, team_axis)
            return order

        if focus is None:
            self._engage_anchors.pop(bot["id"], None)
            if threat_contact is not None:
                observers = threat_contact.get("shootable_by_bot_ids")
                self._set_target(
                    order, bot, threat_contact, profile, personality,
                    bool(threat_contact.get("visible") and
                         bot["id"] in (observers or ())))
                if self._apply_cover_order(
                        order, bot, threat_contact, personality, now,
                        urgent=True, hold_only=low_health):
                    return order
            self._cover_states.pop(bot["id"], None)
            if low_health:
                order["combat_mode"] = "low_health_retreat"
                order["move_position"] = dict(retreat_point)
            elif recent_hit is not None:
                order["combat_mode"] = "under_fire_withdraw"
                order["move_position"] = dict(retreat_point)
            return order

        observers = focus.get("shootable_by_bot_ids")
        locally_shootable = bool(focus.get("visible") and
                                 bot["id"] in (observers or ()))
        self._set_target(
            order, bot, focus, profile, personality, locally_shootable)
        bx = _number(state.get("x"))
        bz = _number(state.get("z"))
        distance = math.hypot(focus["position"]["x"] - bx,
                              focus["position"]["z"] - bz)
        dominant = str(profile.get("dominant_role") or "support")
        roles = profile.get("roles") if isinstance(profile.get("roles"), dict) else {}
        far_limit = desired_range * (1.08 + personality["caution"] * 0.18)
        close_limit = desired_range * (0.48 + personality["aggression"] * 0.10)
        support_score = self._ally_support_score(
            bot, team_bots or (), focus)
        crossfire_risk = self._crossfire_risk(bot, contacts)

        if low_health:
            self._engage_anchors.pop(bot["id"], None)
            if (distance <= fire_range * 1.15 and
                    self._apply_cover_order(
                        order, bot, focus, personality, now,
                        urgent=True, hold_only=True)):
                return order
            self._cover_states.pop(bot["id"], None)
            order["combat_mode"] = "low_health_retreat"
            order["move_position"] = dict(retreat_point)
            order["throttle_override"] = None
            return order

        if recent_hit is not None:
            cover_focus = threat_contact or focus
            cover_observers = cover_focus.get("shootable_by_bot_ids")
            self._set_target(
                order, bot, cover_focus, profile, personality,
                bool(cover_focus.get("visible") and
                     bot["id"] in (cover_observers or ())))
            self._engage_anchors.pop(bot["id"], None)
            if self._apply_cover_order(
                    order, bot, cover_focus, personality, now, urgent=True):
                return order
            self._cover_states.pop(bot["id"], None)
            order["combat_mode"] = "under_fire_withdraw"
            order["move_position"] = dict(retreat_point)
            order["throttle_override"] = None
            return order

        if crossfire_risk >= 0.35 and support_score < 0.70:
            self._engage_anchors.pop(bot["id"], None)
            if (distance <= fire_range * 1.15 and
                    self._apply_cover_order(
                        order, bot, focus, personality, now, urgent=True)):
                return order
            self._cover_states.pop(bot["id"], None)
            order["combat_mode"] = "crossfire_withdraw"
            order["move_position"] = dict(retreat_point)
            order["throttle_override"] = None
            return order

        if not locally_shootable:
            self._engage_anchors.pop(bot["id"], None)
            self._cover_states.pop(bot["id"], None)
            # This branch only handles a leased order whose later observation
            # lost its local lane. Resume the route instead of turning shared
            # team visibility into an unbounded convergence order.
            order["target_id"] = None
            order.pop("target_kind", None)
            order["aim_position"] = dict(move)
            order["face_position"] = dict(move)
            order["fire_allowed"] = False
            order["combat_mode"] = "route"
            order["move_position"] = dict(move)
            order["throttle_override"] = None
        elif (bot["id"] in self._cover_states and
              distance <= fire_range * 1.15 and
              self._apply_cover_order(
                  order, bot, focus, personality, now)):
            self._engage_anchors.pop(bot["id"], None)
        elif distance > far_limit:
            self._engage_anchors.pop(bot["id"], None)
            self._cover_states.pop(bot["id"], None)
            advance_score = (
                personality["aggression"] * 0.85 +
                personality["initiative"] * 0.30 +
                support_score * (0.35 + personality["teamwork"] * 0.25) -
                personality["caution"] * 0.55)
            threshold = 0.24 if dominant == "brawler" else 0.34
            if advance_score >= threshold:
                order["combat_mode"] = "advance_contact"
                order["move_position"] = dict(focus["position"])
                order["throttle_override"] = 0.72
            else:
                order["combat_mode"] = "support_hold"
                order["move_position"] = _point(state)
                order["throttle_override"] = 0.0
        elif (distance <= fire_range * 1.15 and
              self._apply_cover_order(order, bot, focus, personality, now)):
            self._engage_anchors.pop(bot["id"], None)
        elif distance < close_limit and dominant != "brawler":
            # This must precede the general firing-envelope hold below because
            # close_limit is always inside that envelope. Keep firing while a
            # ranged vehicle opens space along its already validated route.
            self._engage_anchors.pop(bot["id"], None)
            self._cover_states.pop(bot["id"], None)
            order["combat_mode"] = "withdraw"
            order["move_position"] = dict(route_anchor)
            order["throttle_override"] = None
        elif distance <= min(fire_range, max(150.0, desired_range * 1.35)):
            # A visible enemy inside an effective firing envelope interrupts
            # route travel immediately when no client-probed cover manoeuvre is
            # active. Existing cover remains a higher-quality combat action.
            self._cover_states.pop(bot["id"], None)
            order["combat_mode"] = "engage"
            target_key = (focus.get("target_kind"), focus["id"])
            anchor_state = self._engage_anchors.get(bot["id"])
            if anchor_state is None or anchor_state["target"] != target_key:
                anchor_state = {
                    "target": target_key,
                    "position": _point(bot["state"]),
                }
                self._engage_anchors[bot["id"]] = anchor_state
            order["move_position"] = dict(anchor_state["position"])
            order["throttle_override"] = 0.0
        elif roles.get("flanker", 0.0) >= 0.68 and personality["initiative"] > 0.42:
            self._engage_anchors.pop(bot["id"], None)
            order["combat_mode"] = "flank"
            order["move_position"] = self._flank_point(bot, focus, desired_range)
            order["throttle_override"] = 0.78
        else:
            order["combat_mode"] = "engage"
            target_key = (focus.get("target_kind"), focus["id"])
            anchor_state = self._engage_anchors.get(bot["id"])
            if anchor_state is None or anchor_state["target"] != target_key:
                anchor_state = {
                    "target": target_key,
                    "position": _point(bot["state"]),
                }
                self._engage_anchors[bot["id"]] = anchor_state
            order["move_position"] = dict(anchor_state["position"])
            # Only the geometry-backed cover state machine may order a peek.
            # Periodic open-ground jiggle looked like a stuck-physics
            # oscillation and could reverse a tank toward an unprobed cliff or
            # traffic queue.
            order["throttle_override"] = 0.0
        self._apply_stationary_angling(
            order, bot, profile, personality)
        return order

    @staticmethod
    def _personality(bot_id):
        # Stable JSON data, not a client object: no process-local RNG state.
        value = (int(bot_id) * 1103515245 + 12345) & 0x7fffffff
        return {
            "aggression": round(0.35 + (value % 41) / 100.0, 3),
            "caution": round(0.25 + ((value >> 8) % 41) / 100.0, 3),
            "teamwork": round(0.30 + ((value >> 12) % 51) / 100.0, 3),
            "patience": round(0.25 + ((value >> 16) % 56) / 100.0, 3),
            "initiative": round(0.25 + ((value >> 20) % 61) / 100.0, 3),
            "adaptability": round(0.30 + ((value >> 4) % 51) / 100.0, 3),
            "jiggle": round(0.18 + ((value >> 6) % 65) / 100.0, 3),
        }
