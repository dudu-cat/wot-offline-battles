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


# In a source checkout ``scripts`` is beside this file. In the downloadable
# client package it lives under the drag-and-drop ``0.8.2`` directory. Add that
# release layout explicitly so running lan_battle_server.py from the package
# root works without asking users to configure PYTHONPATH.
_RELEASE_CLIENT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.8.2")
if os.path.isdir(os.path.join(_RELEASE_CLIENT_ROOT, "scripts")):
    if _RELEASE_CLIENT_ROOT not in sys.path:
        sys.path.insert(0, _RELEASE_CLIENT_ROOT)

from scripts.client.gui.mods.offhangar.bot_ai_cover import (
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
ARTILLERY_ENGAGEMENT_RANGE = 1200.0
ARTILLERY_BLOCKED_RELOCATE_SECONDS = 12.0
ARTILLERY_IDLE_RELOCATE_SECONDS = 65.0
ARTILLERY_ARRIVAL_RADIUS = 9.0


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
        self._artillery_states = {}

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
        self._artillery_states = {}

    def report_contacts(self, contacts, known_targets, now):
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
                    # ``None`` preserves protocol-v5 compatibility with an older
                    # authority client.  A current client always sends the list,
                    # including an empty one when the target is only team-spotted.
                    "shootable_by_bot_ids": self._bot_id_list(
                        raw.get("shootable_by_bot_ids"))
                        if "shootable_by_bot_ids" in raw else None,
                }
                accepted += 1
            elif previous is not None:
                previous["visible"] = False
                previous["shootable_by_bot_ids"] = []
                accepted += 1
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

    def build_orders(self, manifest, bot_states, players, now):
        known_targets = self.known_targets(bot_states, players)
        contacts = self._prune_contacts(known_targets, now)
        bots = self._alive_bots(manifest, bot_states)
        self._prune_tactical_state(bots, known_targets, now)
        self._cover_reservations = set()
        orders = []
        for team in (1, 2):
            team_bots = sorted((bot for bot in bots if bot["team"] == team),
                               key=lambda value: value["id"])
            self._rebalance_routes(team, team_bots, contacts[team], now)
            assignments = self._assign_targets(team_bots, contacts[team], now)
            for index, bot in enumerate(team_bots):
                orders.append(self._order_for(
                    bot, index, len(team_bots), assignments.get(bot["id"]),
                    contacts[team], now))
        orders.sort(key=lambda value: value["id"])
        payload = {"orders": orders}
        signature_orders = []
        for order in orders:
            signature = dict(order)
            mode = str(signature.get("combat_mode") or "")
            if mode in ("artillery_hold", "artillery_relocate",
                        "artillery_fire"):
                # Artillery anchors describe the hull's latest reported pose,
                # not a new tactical command.  Including them made two parked
                # SPGs publish a complete 29-bot order revision every authority
                # frame even though their emplacement and mode were unchanged.
                signature.pop("route_anchor", None)
                if mode == "artillery_fire":
                    # A firing SPG is explicitly held at zero throttle and the
                    # authority resolves its live rendered pose locally.
                    signature.pop("move_position", None)
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
        for bot_id in list(self._artillery_states):
            if bot_id not in live_bots:
                del self._artillery_states[bot_id]
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
            return max(ARTILLERY_ENGAGEMENT_RANGE,
                       _number(profile.get("fire_range"), 0.0))
        mobility = max(_number(roles.get("scout")),
                       _number(roles.get("flanker")))
        if contact.get("visible"):
            distance = max(340.0, min(560.0,
                                     desired * 2.0 + mobility * 300.0))
        else:
            distance = max(240.0, min(420.0,
                                     desired * 1.5 + mobility * 210.0))
        return distance

    def _assign_targets(self, bots, contacts, now):
        """Assign only locally shootable contacts, with a hard focus cap.

        Team spotting is shared intelligence, not proof that every tank has a
        firing lane.  The authority client reports the bot ids whose own LOS
        probe succeeded.  Older protocol-v5 clients omit that field and retain
        their legacy behaviour so mixed packages fail compatibly rather than
        silently disabling combat.
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
                observers = contact.get("shootable_by_bot_ids")
                locally_shootable = (contact.get("visible") and
                                     (observers is None or bot["id"] in observers))
                if not locally_shootable:
                    continue
                distance = math.hypot(
                    contact["position"]["x"] - bx,
                    contact["position"]["z"] - bz)
                if distance > self._engagement_range(bot, contact):
                    continue
                score = 0.0
                score += contact["health"] / float(max(1, contact["max_health"])) * 28.0
                score += distance * 0.018
                if distance <= CLOSE_THREAT_DISTANCE:
                    # A point-blank enemy is an immediate self-defence problem, even
                    # while a previous long-range target still owns a short lease.
                    score -= CLOSE_THREAT_SCORE_BONUS
                candidate = (score, bot["id"], contact["id"], bot, contact, distance)
                candidates.append(candidate)
                by_bot.setdefault(bot["id"], []).append(candidate)

        # Preserve a valid target through small score changes. Without this,
        # sub-metre motion between two similar enemies flips the order every
        # server tick and repeatedly cancels the client's private A* search.
        for bot in bots:
            bot_candidates = sorted(by_bot.get(bot["id"], ()))
            if not bot_candidates:
                self._target_assignments.pop(bot["id"], None)
                continue
            previous = self._target_assignments.get(bot["id"])
            if not isinstance(previous, dict):
                continue
            previous_candidate = None
            for candidate in bot_candidates:
                contact = candidate[4]
                key = (contact.get("target_kind"), contact["id"])
                if key == previous.get("target"):
                    previous_candidate = candidate
                    break
            if previous_candidate is None:
                self._target_assignments.pop(bot["id"], None)
                continue
            best_score = bot_candidates[0][0]
            lease_expired = _number(now) >= _number(previous.get("until"))
            best_candidate = bot_candidates[0]
            best_key = (best_candidate[4].get("target_kind"),
                        best_candidate[4]["id"])
            close_override = (
                best_key != previous.get("target") and
                best_candidate[5] <= CLOSE_THREAT_DISTANCE and
                previous_candidate[5] > CLOSE_THREAT_DISTANCE
            )
            if close_override:
                continue
            if (lease_expired and
                    previous_candidate[0] > best_score + TARGET_SWITCH_MARGIN):
                continue
            contact = previous_candidate[4]
            key = (contact.get("target_kind"), contact["id"])
            if reservations.get(key, 0) >= self._focus_limit(
                    contact, previous_candidate[5]):
                continue
            reservations[key] = reservations.get(key, 0) + 1
            assigned[bot["id"]] = contact
            if lease_expired:
                previous["until"] = _number(now) + TARGET_LEASE_SECONDS
        for unused_score, unused_bot_id, unused_target_id, bot, contact, distance in sorted(candidates):
            if bot["id"] in assigned:
                continue
            key = (contact.get("target_kind"), contact["id"])
            if reservations.get(key, 0) >= self._focus_limit(contact, distance):
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

    def _rebalance_routes(self, team, bots, contacts, now):
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
            assignment = self._route_assignments.get(bot["id"], {})
            current = assignment.get("route") if isinstance(assignment, dict) else None
            if not isinstance(current, dict):
                current = bot.get("route") or {}
            current_id = str(current.get("id") or "")
            if current_id == target_route or counts.get(current_id, 0) <= 1:
                continue
            if str(bot.get("profile", {}).get("class_tag") or "") == "SPG":
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
            return route_id, 0, point, point
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
        if state.get("join_index") == index and isinstance(state.get("join_anchor"), dict):
            anchor = _point(state["join_anchor"])
        else:
            anchor = _point(waypoints[max(0, index - 1)])
        return route_id, index, point, anchor

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
    def _shell_index(profile, contact, personality):
        shells = profile.get("shells") if isinstance(profile.get("shells"), list) else []
        if not shells:
            return 0
        armor = max(0.0, _number(contact.get("armor")))
        best = None
        for shell in shells:
            penetration = max(0.0, _number(shell.get("penetration")))
            damage = max(0.0, _number(shell.get("damage")))
            kind = str(shell.get("kind") or "").lower()
            explosive = "explosive" in kind and "hollow" not in kind
            score = (42.0 if penetration >= armor * 1.08 else -abs(penetration - armor) * 0.4)
            score += damage * 0.04
            if explosive and contact.get("health", 0) <= damage * (0.72 + personality["aggression"] * 0.18):
                score += 55.0
            elif explosive:
                score -= 25.0
            candidate = (score, -_integer(shell.get("index")), shell)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        return max(0, _integer(best[2].get("index"))) if best is not None else 0

    def _cover_candidate(self, bot, focus, personality, now):
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

    def _apply_cover_order(self, order, bot, focus, personality, now):
        selected_state = self._cover_candidate(bot, focus, personality, now)
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
        if phase in ("approach", "return") and cover_distance <= 4.5:
            completed_return = phase == "return"
            phase = "hold"
            state["phase"] = phase
            if completed_return:
                state["refresh_candidate"] = True
            state["phase_until"] = (_number(now) + 0.65 +
                                    personality["patience"] * 1.35)
        elif phase == "hold" and _number(now) >= _number(state.get("phase_until")):
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
            order["throttle_override"] = None
        elif phase == "hold":
            order["combat_mode"] = "cover_hold"
            order["move_position"] = dict(cover)
            order["throttle_override"] = 0.0
        elif phase == "peek":
            order["combat_mode"] = "cover_peek"
            order["move_position"] = dict(peek)
            order["throttle_override"] = None if peek_distance > 4.5 else 0.0
            order["fire_allowed"] = can_fire
        else:
            order["combat_mode"] = "cover_return"
            order["move_position"] = dict(cover)
            order["throttle_override"] = None
        return True

    @staticmethod
    def _artillery_emplacements(bot, home=None):
        """Return rear, annotated positions without inventing map geometry."""
        state = bot.get("state") if isinstance(bot.get("state"), dict) else {}
        home = _point(home if isinstance(home, dict) else state)
        candidates = [home]
        route = bot.get("route") if isinstance(bot.get("route"), dict) else {}
        waypoints = route.get("waypoints") if isinstance(route.get("waypoints"), list) else []
        for raw in waypoints[:3]:
            point = _point(raw)
            distance = math.hypot(point["x"] - home["x"],
                                  point["z"] - home["z"])
            if distance > 220.0:
                continue
            if all(math.hypot(point["x"] - old["x"],
                              point["z"] - old["z"]) > 12.0
                   for old in candidates):
                candidates.append(point)
        return candidates

    def _artillery_order(self, order, bot, focus, contacts, now):
        """Hold a rear firing point and relocate only when its arc is unhelpful.

        The authority client is the geometry oracle: an SPG appears in a
        contact's shootable list only when its real ballistic arc is clear.
        Therefore a visible contact with no assigned focus means this firing
        point is blocked, not that the artillery should drive to the front.
        """
        bot_id = bot["id"]
        current = _point(bot.get("state"))
        state = self._artillery_states.get(bot_id)
        if state is None:
            home = dict(current)
            state = {
                "index": 0,
                "position": dict(home),
                "candidates": self._artillery_emplacements(bot, home),
                "next_relocate": _number(now) + ARTILLERY_IDLE_RELOCATE_SECONDS,
            }
            self._artillery_states[bot_id] = state
        candidates = state.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            candidates = [dict(current)]
            state["candidates"] = candidates

        if focus is not None:
            order["target_id"] = focus["id"]
            order["target_kind"] = focus.get("target_kind")
            order["aim_position"] = dict(focus["position"])
            order["face_position"] = dict(focus["position"])
            observers = focus.get("shootable_by_bot_ids")
            order["fire_allowed"] = bool(
                focus.get("visible") and
                (observers is None or bot_id in observers))
            order["shell_index"] = self._shell_index(
                order["profile"], focus, order["personality"])
            if order["fire_allowed"]:
                # A newly acquired clear arc is a better emplacement than the
                # scheduled destination. Stop here, settle the gun and shoot.
                state["position"] = dict(current)
                state["next_relocate"] = (
                    _number(now) + ARTILLERY_IDLE_RELOCATE_SECONDS)
                order["move_position"] = dict(current)
                order["route_anchor"] = dict(current)
                order["throttle_override"] = 0.0
                order["combat_mode"] = "artillery_fire"
                return order

        visible_contacts = [value for value in contacts
                            if value.get("visible")]
        interval = (ARTILLERY_BLOCKED_RELOCATE_SECONDS
                    if visible_contacts else ARTILLERY_IDLE_RELOCATE_SECONDS)
        if visible_contacts:
            state["next_relocate"] = min(
                _number(state.get("next_relocate"), now + interval),
                _number(now) + ARTILLERY_BLOCKED_RELOCATE_SECONDS)
        if (_number(now) >= _number(state.get("next_relocate")) and
                len(candidates) > 1):
            state["index"] = (int(state.get("index", 0)) + 1) % len(candidates)
            state["position"] = dict(candidates[state["index"]])
            state["next_relocate"] = _number(now) + interval

        destination = _point(state.get("position"))
        distance = math.hypot(destination["x"] - current["x"],
                              destination["z"] - current["z"])
        order["move_position"] = destination
        order["route_anchor"] = dict(current)
        order["fire_allowed"] = False
        if focus is None:
            order["target_id"] = None
            order.pop("target_kind", None)
            order["aim_position"] = destination
            order["face_position"] = destination
        if distance > ARTILLERY_ARRIVAL_RADIUS:
            order["combat_mode"] = "artillery_relocate"
            order["throttle_override"] = None
        else:
            order["combat_mode"] = "artillery_hold"
            order["throttle_override"] = 0.0
        return order

    def _order_for(self, bot, index, count, focus, contacts, now):
        route_id, route_index, move, route_anchor = self._route(bot, now)
        profile = dict(bot["profile"])
        desired_range = max(10.0, _number(profile.get("desired_range"), 180.0))
        fire_range = max(desired_range, _number(profile.get("fire_range"), 500.0))
        personality = self._personality(bot["id"])
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
            "personality": personality,
            "profile": profile,
            "shell_index": 0,
        }
        if str(profile.get("class_tag") or "") == "SPG":
            return self._artillery_order(order, bot, focus, contacts, now)
        if focus is None:
            self._engage_anchors.pop(bot["id"], None)
            self._cover_states.pop(bot["id"], None)
            return order
        order["target_id"] = focus["id"]
        order["target_kind"] = focus.get("target_kind")
        order["aim_position"] = dict(focus["position"])
        order["face_position"] = dict(focus["position"])
        observers = focus.get("shootable_by_bot_ids")
        locally_shootable = bool(focus.get("visible") and
                                 (observers is None or bot["id"] in observers))
        order["fire_allowed"] = locally_shootable
        order["shell_index"] = self._shell_index(profile, focus, personality)
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        distance = math.hypot(focus["position"]["x"] - bx,
                              focus["position"]["z"] - bz)
        dominant = str(profile.get("dominant_role") or "support")
        roles = profile.get("roles") if isinstance(profile.get("roles"), dict) else {}
        far_limit = desired_range * (1.08 + personality["caution"] * 0.18)
        close_limit = desired_range * (0.48 + personality["aggression"] * 0.10)
        if not locally_shootable:
            self._engage_anchors.pop(bot["id"], None)
            self._cover_states.pop(bot["id"], None)
            # This branch is retained for a legacy order already in flight. New
            # assignments require local LOS, so a team-only red dot cannot turn
            # the hull, stop a casemate vehicle, or start a cover manoeuvre.
            order["target_id"] = None
            order.pop("target_kind", None)
            order["aim_position"] = dict(move)
            order["face_position"] = dict(move)
            order["fire_allowed"] = False
            order["combat_mode"] = "route"
            order["move_position"] = dict(move)
            order["throttle_override"] = None
        elif distance > far_limit:
            self._engage_anchors.pop(bot["id"], None)
            self._cover_states.pop(bot["id"], None)
            order["combat_mode"] = "advance_contact"
            order["move_position"] = dict(focus["position"])
            order["throttle_override"] = None
        elif (distance <= fire_range * 1.15 and
              self._apply_cover_order(order, bot, focus, personality, now)):
            self._engage_anchors.pop(bot["id"], None)
        elif distance < close_limit and dominant != "brawler":
            # A support or ranged vehicle that has been rushed should keep
            # aiming and firing while opening the distance. This must precede
            # the general firing-envelope hold below: close_limit is always
            # inside that envelope, so placing it later makes it unreachable.
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
            order["throttle_override"] = None
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
            # Periodic open-ground jiggle looked like a stuck-physics oscillation
            # and could reverse a tank toward an unprobed cliff or traffic queue.
            order["throttle_override"] = 0.0
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
