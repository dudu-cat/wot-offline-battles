"""Pure-data tactical planner for the LAN bot authority.

The planner deliberately has no socket, BigWorld, or client imports.  Its
input and output are JSON-compatible dictionaries so an eventual Go service
can preserve the same contract.  Enemy data is accepted only through
``report_contacts``; players and bot state are used to validate identities,
never to invent a target position.
"""

import math


CONTACT_TTL_SECONDS = 8.0
MAX_CONTACTS_PER_TEAM = 32


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
        self._route_states = {}
        self._engage_anchors = {}

    def reset(self):
        self.revision = 0
        self._contacts = {1: {}, 2: {}}
        self._last_orders = None
        self._route_states = {}
        self._engage_anchors = {}

    def report_contacts(self, contacts, known_targets, now):
        """Store only authority-reported observations after identity checks.

        ``known_targets`` maps an id to ``{"team": int, "alive": bool}``.
        Reporting a contact never looks up its target's live pose, which keeps
        this server from becoming omniscient.
        """
        accepted = 0
        for raw in (contacts or [])[:MAX_CONTACTS_PER_TEAM * 2]:
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
                }
                accepted += 1
            elif previous is not None:
                previous["visible"] = False
                accepted += 1
        return accepted

    def build_orders(self, manifest, bot_states, players, now):
        known_targets = self.known_targets(bot_states, players)
        contacts = self._prune_contacts(known_targets, now)
        bots = self._alive_bots(manifest, bot_states)
        orders = []
        for team in (1, 2):
            team_bots = [bot for bot in bots if bot["team"] == team]
            assignments = self._assign_targets(team_bots, contacts[team])
            for index, bot in enumerate(team_bots):
                orders.append(self._order_for(
                    bot, index, len(team_bots), assignments.get(bot["id"]),
                    contacts[team], now))
        orders.sort(key=lambda value: value["id"])
        payload = {"orders": orders}
        if payload != self._last_orders:
            self.revision += 1
            self._last_orders = payload
        return {"revision": self.revision, "orders": orders}

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

    def _assign_targets(self, bots, contacts):
        """Reserve targets per bot instead of issuing one team-wide dog-pile."""
        if not bots or not contacts:
            return {}
        reservations = {}
        assigned = {}
        for bot in sorted(bots, key=lambda value: value["id"]):
            bx = _number(bot["state"].get("x"))
            bz = _number(bot["state"].get("z"))
            best = None
            best_score = None
            for contact in contacts:
                key = (contact.get("target_kind"), contact["id"])
                reserved = reservations.get(key, 0)
                desired = self._desired_focus(contact)
                distance = math.hypot(
                    contact["position"]["x"] - bx,
                    contact["position"]["z"] - bz)
                score = (0.0 if contact.get("visible") else 42.0)
                score += contact["health"] / float(max(1, contact["max_health"])) * 28.0
                score += distance * 0.018
                if reserved >= desired:
                    score += (reserved - desired + 1) * 32.0
                else:
                    score -= reserved * 3.0
                candidate = (score, contact["id"], contact)
                if best_score is None or candidate[:2] < best_score:
                    best_score = candidate[:2]
                    best = contact
            if best is not None:
                key = (best.get("target_kind"), best["id"])
                reservations[key] = reservations.get(key, 0) + 1
                assigned[bot["id"]] = best
        return assigned

    def _route(self, bot, now):
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
        state = self._route_states.setdefault(
            bot["id"], {"index": 0, "hold_until": 0.0})
        index = min(max(0, _integer(state.get("index"))), len(waypoints) - 1)
        point = _point(waypoints[index])
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        reached = math.hypot(point["x"] - bx, point["z"] - bz) <= 13.0
        holding = False
        if reached and bool(waypoints[index].get("hold", False)):
            if _number(state.get("hold_until")) <= 0.0:
                state["hold_until"] = _number(now) + 4.0 + self._personality(bot["id"])["patience"] * 5.0
            holding = _number(now) < _number(state["hold_until"])
        if reached and not holding and index + 1 < len(waypoints):
            index += 1
            state["index"] = index
            state["hold_until"] = 0.0
            point = _point(waypoints[index])
        anchor = _point(waypoints[max(0, index - 1)])
        return str(route.get("id") or "uploaded_route"), index, point, anchor, holding

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

    def _order_for(self, bot, index, count, focus, contacts, now):
        route_id, route_index, move, route_anchor, holding = self._route(bot, now)
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
            "combat_mode": "hold" if holding else "advance",
            "throttle_override": 0.0 if holding else 0.75,
            "desired_range": round(desired_range, 3),
            "fire_range": round(fire_range, 3),
            "route_id": route_id,
            "route_index": route_index,
            "route_anchor": dict(route_anchor),
            "personality": personality,
            "profile": profile,
            "shell_index": 0,
        }
        if focus is None or holding:
            self._engage_anchors.pop(bot["id"], None)
            return order
        order["target_id"] = focus["id"]
        order["target_kind"] = focus.get("target_kind")
        order["aim_position"] = dict(focus["position"])
        order["face_position"] = dict(focus["position"])
        order["fire_allowed"] = bool(focus.get("visible"))
        order["shell_index"] = self._shell_index(profile, focus, personality)
        bx = _number(bot["state"].get("x"))
        bz = _number(bot["state"].get("z"))
        distance = math.hypot(focus["position"]["x"] - bx,
                              focus["position"]["z"] - bz)
        dominant = str(profile.get("dominant_role") or "support")
        roles = profile.get("roles") if isinstance(profile.get("roles"), dict) else {}
        if not focus.get("visible"):
            self._engage_anchors.pop(bot["id"], None)
            order["combat_mode"] = "investigate"
            order["move_position"] = dict(focus["position"])
            order["throttle_override"] = 0.65
        elif roles.get("flanker", 0.0) >= 0.68 and personality["initiative"] > 0.42:
            self._engage_anchors.pop(bot["id"], None)
            order["combat_mode"] = "flank"
            order["move_position"] = self._flank_point(bot, focus, desired_range)
            order["throttle_override"] = 0.78
        elif distance > desired_range * (1.08 + personality["caution"] * 0.18):
            self._engage_anchors.pop(bot["id"], None)
            order["combat_mode"] = "advance_contact"
            order["move_position"] = dict(focus["position"])
            order["throttle_override"] = 0.72
        elif distance < desired_range * (0.48 + personality["aggression"] * 0.10) and dominant != "brawler":
            self._engage_anchors.pop(bot["id"], None)
            order["combat_mode"] = "withdraw"
            order["move_position"] = dict(route_anchor)
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
            if dominant in ("brawler", "support") and personality["jiggle"] > 0.62:
                phase = (_number(now) + bot["id"] * 0.071) % 3.2
                order["throttle_override"] = 0.38 if phase < 1.55 else -0.30
                order["combat_mode"] = "jiggle_forward" if phase < 1.55 else "jiggle_back"
            else:
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
