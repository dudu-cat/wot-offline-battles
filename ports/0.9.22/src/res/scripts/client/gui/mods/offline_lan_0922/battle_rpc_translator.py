"""Translate explicit battle_rpc output to existing LAN protocol v5 messages.

Only ``input`` fields accepted by lan_battle_server.py are emitted.  Avatar
setup, binding, VOIP and local settings have no v5 server message and are
reported as unsupported rather than silently serialized.
"""


class BattleRpcTranslator(object):
    _FORWARD = 1
    _BACKWARD = 2
    _LEFT = 4
    _RIGHT = 8

    def __init__(self, lan_client, pose_getter=None):
        self.client = lan_client
        self.pose_getter = pose_getter
        self.forward = 0.0
        self.turn = 0.0
        self.aim_yaw = 0.0
        self.gun_pitch = 0.0

    def _pose(self):
        if not callable(self.pose_getter):
            return None, None
        value = self.pose_getter()
        try:
            return value[0], value[1]
        except (TypeError, IndexError):
            return None, None

    def _send_input(self):
        position, yaw = self._pose()
        return self.client.send_input(self.forward, self.turn, self.aim_yaw,
                                      self.gun_pitch, position, yaw)

    def translate(self, message):
        if not isinstance(message, dict) or message.get('kind') != 'battle_rpc':
            return False
        method = message.get('method')
        if method in ('vehicle_moveWith', 'moveWith'):
            flags = int(message.get('flags', 0))
            self.forward = 1.0 if flags & self._FORWARD else (
                -1.0 if flags & self._BACKWARD else 0.0)
            self.turn = 1.0 if flags & self._RIGHT else (
                -1.0 if flags & self._LEFT else 0.0)
            return self._send_input()
        if method in ('vehicle_trackWorldPointWithGun', 'trackWorldPointWithGun'):
            point = message.get('point') or {}
            try:
                import math
                position, unused_yaw = self._pose()
                if position is None:
                    return False
                self.aim_yaw = math.atan2(float(point['x']) - float(position[0]),
                                          float(point['z']) - float(position[2]))
            except (KeyError, TypeError, ValueError):
                return False
            return self._send_input()
        if method in ('vehicle_stopTrackingWithGun', 'stopTrackingWithGun'):
            self.aim_yaw = float(message.get('turret_yaw', 0.0))
            self.gun_pitch = float(message.get('gun_pitch', 0.0))
            return self._send_input()
        if method in ('vehicle_shoot', 'shoot'):
            position, yaw = self._pose()
            return self.client.send_fire(0, position, yaw, self.aim_yaw,
                                         self.gun_pitch)
        return False
