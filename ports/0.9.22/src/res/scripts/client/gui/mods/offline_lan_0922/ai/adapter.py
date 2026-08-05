"""Plain-data adapter between a battle runtime and the pure bot planner.

The adapter deliberately does not inspect BigWorld entities.  A caller sends
JSON-like dictionaries and receives a JSON-safe order: a route/goal, a local
movement command, and optional fire intent.  The caller remains responsible
for visibility, collision probes, and applying commands to any client entity.
"""

from gui.mods.offline_lan_0922.ai.driver import LocalDriver
from gui.mods.offline_lan_0922.ai.planner import BattleDirector


def _position(value, fallback=(0.0, 0.0, 0.0)):
    if isinstance(value, dict):
        value = (value.get('x'), value.get('y'), value.get('z'))
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, IndexError):
        return fallback


def _contact(value):
    if not isinstance(value, dict):
        return None
    result = dict(value)
    result['position'] = _position(value.get('position'))
    result['visible'] = bool(value.get('visible', False))
    return result


class BotAdapter(object):
    """Owns pure planner/driver state for one map and battle seed."""

    def __init__(self, map_name, battle_seed, bases=None, bounds=None):
        self.director = BattleDirector(map_name, battle_seed, bases, bounds)
        self.driver = LocalDriver()

    def register(self, bot_id, team, descriptor, display_name='Bot'):
        return self.director.register(bot_id, team, descriptor, display_name)

    def forget(self, bot_id):
        self.driver.forget(bot_id)
        self.director.agents.pop(int(bot_id), None)

    def decide(self, state, direction_clear):
        """Return a deterministic, serializable command for one bot.

        ``state`` needs ``id``, ``position``, ``yaw``, ``speed``, ``dt``,
        ``now`` and optionally ``health``, ``max_health``, ``contacts`` and
        ``neighbours``.  ``direction_clear(yaw)`` is the sole runtime probe.
        """
        state = state if isinstance(state, dict) else {}
        bot_id = int(state.get('id', 0))
        position = _position(state.get('position'))
        contacts = [_contact(item) for item in state.get('contacts', ())]
        contacts = [item for item in contacts if item is not None]
        team = self.director.agents[bot_id]['team']
        now = float(state.get('now', 0.0))
        for contact in contacts:
            self.director.update_contact(
                team, contact.get('id', 0), contact.get('team', 0),
                contact['position'], contact.get('health', 1.0),
                contact.get('max_health', 1.0), contact.get('class_tag'),
                contact['visible'], now, contact.get('armor', 0.0),
                contact.get('speed', 0.0))
        strategic = self.director.order_for(
            bot_id, position, float(state.get('yaw', 0.0)),
            state.get('health', 1.0), state.get('max_health', 1.0), now)
        return self._drive_order(
            bot_id, state, position, strategic, direction_clear)

    def decide_with_order(self, state, strategic, direction_clear):
        """Apply a server macro order through the same local terrain driver."""
        state = state if isinstance(state, dict) else {}
        strategic = strategic if isinstance(strategic, dict) else {}
        bot_id = int(state.get('id', 0))
        position = _position(state.get('position'))
        return self._drive_order(
            bot_id, state, position, strategic, direction_clear)

    def _drive_order(self, bot_id, state, position, strategic,
                     direction_clear):
        target = _position(strategic.get('move_position'), position)
        local = self.driver.drive(
            bot_id, position, float(state.get('yaw', 0.0)),
            float(state.get('speed', 0.0)), float(state.get('dt', 0.0)),
            target, state.get('neighbours', ()), direction_clear,
            velocity=state.get('velocity'))
        result = {
            'bot_id': bot_id,
            'target_id': strategic.get('target_id'),
            'aim_position': _position(
                strategic.get('aim_position'), target),
            'fire_range': float(strategic.get('fire_range', 0.0)),
            'move_position': target,
            'combat_mode': strategic.get('combat_mode', 'route'),
            'fire_allowed': bool(strategic.get('fire_allowed', False)),
            'shell_index': int(strategic.get('shell_index', 0)),
            'throttle': float(local['throttle']),
            'turn': float(local['turn']),
            'target_yaw': float(local['target_yaw']),
            'recovery_mode': local['recovery_mode'],
        }
        throttle_override = strategic.get('throttle_override')
        if throttle_override is not None:
            result['throttle'] = max(
                -1.0, min(1.0, float(throttle_override)))
        return result
