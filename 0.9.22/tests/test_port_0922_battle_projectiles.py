import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.projectile_manager import InFlightProjectiles
from gui.mods.offline_lan_0922 import combat_rules


class _Vector(object):
    def __init__(self, value=(0.0, 0.0, 0.0), y=None, z=None):
        if y is not None and z is not None:
            value = (value, y, z)
        try:
            value = (value.x, value.y, value.z)
        except AttributeError:
            pass
        self.x, self.y, self.z = map(float, value)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return _Vector((self.x + other.x, self.y + other.y,
                        self.z + other.z))

    def __sub__(self, other):
        return _Vector((self.x - other.x, self.y - other.y,
                        self.z - other.z))

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)

    def scale(self, value):
        return _Vector((self.x * value, self.y * value, self.z * value))


class _BigWorld(object):
    def __init__(self):
        self.now = 0.0
        self.wall_x = None

    def time(self):
        return self.now

    def wg_collideSegment(self, unused_space, start, end, unused_mask):
        if self.wall_x is None:
            return None
        low = min(start.x, end.x)
        high = max(start.x, end.x)
        if not low <= self.wall_x <= high or abs(end.x - start.x) < 1e-9:
            return None
        fraction = (self.wall_x - start.x) / (end.x - start.x)
        return (_Vector((
            self.wall_x,
            start.y + (end.y - start.y) * fraction,
            start.z + (end.z - start.z) * fraction)), None)


class _Client(object):
    def __init__(self):
        self.authority_epoch = 1
        self.server_time_ms = 0
        self.resolutions = []
        self.progress = []
        self.launches = []

    def is_bot_authority(self):
        return True

    def send_projectile_resolve(self, *args, **kwargs):
        self.resolutions.append((args, kwargs))
        return True

    def send_projectile_progress(self, epoch, cursors):
        self.progress.append((epoch, cursors))
        return True

    def send_projectile_launch(self, *args, **kwargs):
        self.launches.append((args, kwargs))
        return int(args[2])


def _battle(now=0.0):
    bigworld = _BigWorld()
    bigworld.now = now
    runtime = types.SimpleNamespace(
        bigworld=bigworld,
        math=types.SimpleNamespace(Vector3=_Vector))
    battle = BattleRuntime(runtime)
    battle.client = _Client()
    battle._avatar = types.SimpleNamespace(spaceID=1)
    battle._projectiles = InFlightProjectiles(initial_time=now)
    battle._projectile_meta = {}
    battle._projectile_visual_meta = {}
    battle._projectile_terminal_data = {}
    battle._projectile_target_positions = {}
    battle._projectile_position_history = []
    battle._projectile_server_time_ms = 0
    battle._projectile_server_local_time = now
    battle._projectile_epoch = 1
    battle._next_projectile_progress_time = now
    shot = types.SimpleNamespace(
        shell=types.SimpleNamespace(kind='ARMOR_PIERCING'),
        maxDistance=100.0)
    descriptor = types.SimpleNamespace(
        gun=types.SimpleNamespace(shots=[shot]))
    source = types.SimpleNamespace(
        id=41, isStarted=True, typeDescriptor=descriptor,
        position=_Vector((0.0, 0.0, 0.0)), isAlive=lambda: True)
    battle._records = {
        'player:7': {
            'engine_id': 41, 'network_id': 7, 'kind': 'player',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}}
    battle._server_entity = lambda entity_id: source if entity_id == 41 else None
    return battle, bigworld


def _event():
    return {
        'kind': 'shot', 'attacker': 7,
        'projectile_id': 'player:7:1',
        'shooter_kind': 'player', 'shooter_id': 7,
        'source_vehicle': 'ussr:R11_MS-1',
        'shot_seq': 1, 'shell_index': 0,
        'origin': [0.0, 1.0, 0.0],
        'velocity': [10.0, 0.0, 0.0],
        'gravity': 0.000001, 'maxDistance': 100.0,
        'max_time_ms': 20000, 'is_he': False,
        'splash_radius': 0.0, 'penetration_factor': 1.0,
        'launch_server_time_ms': 0, 'authority_epoch': 1,
    }


class BattleProjectileTests(unittest.TestCase):
    def test_bot_authority_change_invalidates_artillery_proofs_once(self):
        battle, unused_bigworld = _battle()

        class _Bots(object):
            def __init__(self):
                self.authority_id = 1

            def battle_start(self, start):
                self.authority_id = start.get('bot_authority_id')
                return []

            def is_authority(self):
                return False

        battle._bots = _Bots()
        battle._artillery = types.SimpleNamespace(reset=mock.Mock())
        battle._start_message = {
            'round_id': 1, 'bot_authority_id': 1, 'bot_manifest': []}
        battle._last_snapshot = {'bots': []}

        self.assertFalse(battle._reconcile_bot_authority(1))
        battle._artillery.reset.assert_not_called()
        self.assertTrue(battle._reconcile_bot_authority(2))
        battle._artillery.reset.assert_called_once_with()
        self.assertFalse(battle._reconcile_bot_authority(2))
        battle._artillery.reset.assert_called_once_with()

    def test_artillery_final_probe_uses_exact_native_muzzle(self):
        battle, unused_bigworld = _battle()
        muzzle = _Vector((3.0, 4.0, 5.0))
        source = types.SimpleNamespace(
            isStarted=True, typeDescriptor=object(),
            model=types.SimpleNamespace(node=lambda unused: types.SimpleNamespace(
                translation=muzzle)))
        battle._records['bot:11'] = {'engine_id': 77}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)
        battle._runtime.math.Matrix = lambda node: node
        receipt = {'proof_key': ('launch',)}
        battle._artillery = types.SimpleNamespace(
            request_launch=mock.Mock(return_value=(True, receipt)))

        result = battle._bot_artillery_launch(
            {'id': 11}, {'kind': 'player', 'network_id': 7}, object(),
            0, 4, 0.25, 0.15, 2.0, 10.0)

        self.assertIs(receipt, result)
        args = battle._artillery.request_launch.call_args[0]
        self.assertEqual((3.0, 4.0, 5.0), args[5])
        self.assertEqual((4, 0.25, 0.15, 2.0, 10.0), args[4:5] + args[6:])

    def test_artillery_cancel_discards_the_controller_launch_slot(self):
        battle, unused_bigworld = _battle()
        battle._artillery = types.SimpleNamespace(
            cancel_launch=mock.Mock(return_value=True))
        source = {'id': 11, 'x': 1.0, 'y': 2.0, 'z': 3.0}

        self.assertTrue(battle._bot_artillery_cancel(source))
        battle._artillery.cancel_launch.assert_called_once_with(source)

    def test_bot_launch_without_native_muzzle_fails_closed(self):
        battle, unused_bigworld = _battle()
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(kind='ARMOR_PIERCING'),
            speed=100.0, gravity=9.81, maxDistance=500.0)
        descriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[shot]))
        source = types.SimpleNamespace(
            isStarted=True, typeDescriptor=descriptor,
            position=_Vector((4.0, 5.0, 6.0)),
            model=types.SimpleNamespace(node=mock.Mock(
                side_effect=RuntimeError('native muzzle unavailable'))))
        battle._records['bot:11'] = {'engine_id': 77}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)

        self.assertFalse(battle._launch_bot_projectile({
            'id': 11, 'profile': {'class_tag': 'MT'},
            'shot_yaw': 0.0, 'shot_pitch': 0.0, 'shell_index': 0,
        }, 1))
        self.assertEqual([], battle.client.launches)

    def test_spg_launch_reuses_the_proved_origin_and_velocity(self):
        battle, unused_bigworld = _battle()
        speed = 10.0
        yaw = 0.25
        pitch = 0.15
        horizontal = math.cos(pitch)
        origin = (3.0, 4.0, 5.0)
        velocity = (
            math.sin(yaw) * horizontal * speed,
            math.sin(pitch) * speed,
            math.cos(yaw) * horizontal * speed)
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(kind='ARMOR_PIERCING'),
            speed=speed, gravity=9.81, maxDistance=100.0)
        descriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[shot]))
        source = types.SimpleNamespace(
            isStarted=True, typeDescriptor=descriptor,
            model=types.SimpleNamespace(node=mock.Mock(
                side_effect=AssertionError('SPG launch re-sampled muzzle'))))
        battle._records['bot:11'] = {
            'engine_id': 77, 'network_id': 11, 'kind': 'bot'}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)
        proof = (
            'launch', 11, 'player', 7, 0, 4, origin,
            yaw, pitch, speed, 9.81, 100.0, 2.0)
        state = {
            'id': 11, 'profile': {'class_tag': 'SPG'},
            'fire_seq': 4, 'shell_index': 0,
            'shot_yaw': yaw, 'shot_pitch': pitch,
            'shot_origin': origin, 'shot_velocity': velocity,
            'shot_gravity': 9.81, 'shot_max_distance': 100.0,
            'shot_max_time_ms': 20000, 'shot_proof_key': proof,
        }

        self.assertTrue(battle._launch_bot_projectile(state, 4))
        args, unused_kwargs = battle.client.launches[-1]
        self.assertEqual(list(origin), args[4])
        self.assertEqual(list(velocity), args[5])
        self.assertEqual(20000, args[8])
        source.model.node.assert_not_called()

        state['shot_velocity'] = (velocity[0] + 0.01,) + velocity[1:]
        battle.client.launches = []
        self.assertFalse(battle._launch_bot_projectile(state, 4))
        self.assertEqual([], battle.client.launches)

    def test_spg_without_final_path_receipt_never_launches(self):
        battle, unused_bigworld = _battle()
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(kind='ARMOR_PIERCING'),
            speed=10.0, gravity=9.81, maxDistance=100.0)
        source = types.SimpleNamespace(
            isStarted=True,
            typeDescriptor=types.SimpleNamespace(
                gun=types.SimpleNamespace(shots=[shot])))
        battle._records['bot:11'] = {'engine_id': 77}
        battle._server_entity = lambda entity_id: (
            source if entity_id == 77 else None)

        self.assertFalse(battle._launch_bot_projectile({
            'id': 11, 'profile': {'class_tag': 'SPG'},
            'shot_yaw': 0.0, 'shot_pitch': 0.1, 'shell_index': 0,
        }, 1))
        self.assertEqual([], battle.client.launches)

    def test_receive_timestamp_preserves_launch_age_across_main_thread_stall(self):
        battle, bigworld = _battle(now=11.0)
        with mock.patch.object(
                sys.modules[BattleRuntime.__module__].time,
                'monotonic', return_value=101.0):
            self.assertTrue(battle._observe_projectile_message({
                'server_time_ms': 10000,
                'authority_epoch': 1,
                '_client_received_time': 100.0,
            }))

        self.assertEqual(10.0, battle._projectile_server_local_time)
        self.assertEqual(11000, battle._projectile_estimated_server_time(11.0))

    def test_delayed_tracer_reference_matches_authority_launch_age(self):
        battle, bigworld = _battle(now=1.0)
        source = battle._server_entity(41)
        source.showShooting = mock.Mock(return_value=True)
        battle._remote_factory = types.SimpleNamespace(
            play_projectile_tracer=mock.Mock(return_value=1000000))
        event = dict(
            _event(), maxDistance=1000.0, max_time_ms=20000,
            velocity=[100.0, 20.0, 0.0], gravity=10.0,
            launch_server_time_ms=0)
        battle._projectile_server_time_ms = 1000
        battle._projectile_server_local_time = 1.0

        self.assertTrue(battle._show_shot(event))

        arguments = battle._remote_factory.play_projectile_tracer.call_args[0]
        self.assertEqual('player:7:1', arguments[7])
        self.assertEqual((100.0, 16.0, 0.0), arguments[8])
        self.assertEqual((100.0, 10.0, 0.0), arguments[9])
        source.showShooting.assert_called_once_with(1, False)

    def test_terminal_event_hides_visual_at_authoritative_impact_once(self):
        battle, unused_bigworld = _battle()
        battle._remote_factory = types.SimpleNamespace(
            stop_projectile_tracer=mock.Mock(return_value=True))
        self.assertTrue(battle._accept_projectile_event(_event()))
        event = {
            'kind': 'projectile_impact',
            'projectile_id': 'player:7:1',
            'outcome': 'impact', 'resolved_time_ms': 500,
            'impact': [5.0, 0.875, 0.0],
        }

        self.assertTrue(battle._apply_projectile_terminal_event(event))

        # A terminal whose ground/vehicle verdict is not ours carries no
        # explosion, so the armour-hit effect is never doubled up.
        battle._remote_factory.stop_projectile_tracer.assert_called_once_with(
            'player:7:1', [5.0, 0.875, 0.0], explosion=None)

    def test_a_world_terminal_carries_the_ground_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._remote_factory = types.SimpleNamespace(
            stop_projectile_tracer=mock.Mock(return_value=True))
        battle._projectile_meta['p1'] = {
            'hit_vehicle': False, 'terminal_velocity': (0.0, -100.0, 10.0),
            'shooter_kind': 'player', 'shooter_id': 7, 'shell_index': 0}
        battle._projectile_shot = lambda meta: {
            'shell': {'effectsIndex': 3}}
        battle._surface_effect_material = lambda impact: 'ground'
        battle._runtime.vehicles = types.SimpleNamespace(
            g_cache=types.SimpleNamespace(shotEffects=[
                {}, {}, {}, {'groundHit': ('kp', 'fx', set())}]))

        bundle = battle._projectile_explosion('p1', (1.0, 2.0, 3.0))

        self.assertIsNotNone(bundle)
        effects_descr, material, velocity = bundle
        self.assertEqual({'groundHit': ('kp', 'fx', set())}, effects_descr)
        self.assertEqual('ground', material)
        self.assertEqual(-100.0, velocity[1])

    def test_a_vehicle_terminal_carries_no_ground_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_meta['p1'] = {'hit_vehicle': True}

        self.assertIsNone(battle._projectile_explosion('p1', (1.0, 2.0, 3.0)))

    def test_an_unknown_verdict_carries_no_ground_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_meta['p1'] = {}

        self.assertIsNone(battle._projectile_explosion('p1', (1.0, 2.0, 3.0)))

    def test_an_unresolvable_surface_carries_no_explosion(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle._projectile_meta['p1'] = {
            'hit_vehicle': False, 'terminal_velocity': (0.0, -1.0, 0.0)}
        battle._projectile_shot = lambda meta: {'shell': {'effectsIndex': 0}}
        battle._runtime.vehicles = types.SimpleNamespace(
            g_cache=types.SimpleNamespace(shotEffects=[{'groundHit': ()}]))
        battle._surface_effect_material = lambda impact: None

        self.assertIsNone(battle._projectile_explosion('p1', (1.0, 2.0, 3.0)))

    def test_snapshot_ensures_tracer_even_when_client_is_not_authority(self):
        battle, unused_bigworld = _battle(now=1.0)
        battle.client.is_bot_authority = lambda: False
        battle._resolve_descriptor = lambda unused_name: (
            battle._server_entity(41).typeDescriptor)
        battle._remote_factory = types.SimpleNamespace(
            play_projectile_tracer=mock.Mock(return_value=1000000))
        row = dict(_event(), max_distance=100.0)
        row.pop('maxDistance')
        row.pop('kind')
        row.pop('attacker')
        row.update({
            'checked_through_ms': 0, 'checked_distance': 0.0,
            'piercing_loss': 0.0,
        })

        self.assertTrue(battle._reconcile_projectile_snapshot({
            'projectiles': [row]}))

        self.assertEqual(1, battle._remote_factory.
                         play_projectile_tracer.call_count)
        self.assertFalse(battle._projectiles.contains('player:7:1'))

    def test_canonical_launch_waits_for_trajectory_impact(self):
        battle, bigworld = _battle()
        bigworld.wall_x = 5.0
        self.assertTrue(battle._accept_projectile_event(_event()))

        bigworld.now = 0.4
        self.assertTrue(battle._advance_projectiles(0.4))
        self.assertEqual([], battle.client.resolutions)
        self.assertTrue(battle._projectiles.contains('player:7:1'))
        cursor = battle.client.progress[-1][1][0]
        acknowledged = dict(_event())
        acknowledged['max_distance'] = acknowledged.pop('maxDistance')
        acknowledged.update({
            'checked_through_ms': cursor['checked_through_ms'],
            'checked_distance': cursor['checked_distance'],
            'piercing_loss': cursor['piercing_loss'],
        })
        battle._install_projectile_meta(
            battle._projectile_wire_meta(acknowledged))

        bigworld.now = 0.6
        self.assertTrue(battle._advance_projectiles(0.6))
        self.assertEqual(1, len(battle.client.resolutions))
        args, kwargs = battle.client.resolutions[0]
        self.assertEqual('player:7:1', args[1])
        self.assertEqual('impact', args[3])
        self.assertGreaterEqual(args[4], 499)
        self.assertLessEqual(args[4], 501)
        self.assertAlmostEqual(5.0, args[5][0], places=5)
        self.assertIsNone(args[6])
        self.assertEqual([], args[7])
        self.assertAlmostEqual(5.0, kwargs['checked_distance'], places=4)

    def test_far_records_skip_native_entity_lookup_before_exact_broadphase(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        source = battle._server_entity(41)
        target_ids = []
        current = {'player:7': (0.0, 0.0, 0.0)}
        for index in range(29):
            key = 'bot:%d' % index
            engine_id = 1000 + index
            target_ids.append(engine_id)
            battle._records[key] = {
                'engine_id': engine_id, 'network_id': index, 'kind': 'bot',
                'local': False, 'ready': True,
                'state': {'health': 100, 'alive': True}}
            current[key] = (1000.0 + index, 0.0, 1000.0)
        queried = []

        def server_entity(entity_id):
            queried.append(entity_id)
            return source if entity_id == 41 else None

        battle._server_entity = server_entity
        battle._projectile_current_positions = current
        battle._projectile_position_history = []
        battle._projectile_scan_count = 0
        battle._projectile_candidate_count = 0
        state = battle._projectiles.get('player:7:1')

        self.assertIsNone(battle._projectile_chord(
            state, (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), 0.0, 0.05))
        self.assertEqual(30, battle._projectile_scan_count)
        self.assertEqual(0, battle._projectile_candidate_count)
        self.assertTrue(queried)
        self.assertFalse(set(target_ids).intersection(queried))

    def test_stock_max_29_projectile_debt_bounds_frame_and_reduces_scans(self):
        battle, bigworld = _battle()
        source = battle._server_entity(41)
        entities = {41: source}
        for index in range(29):
            engine_id = 1000 + index
            entities[engine_id] = types.SimpleNamespace(
                id=engine_id, isStarted=True,
                position=_Vector((1000.0 + index, 0.0, 1000.0)),
                isAlive=lambda: True,
                collideSegmentExt=mock.Mock(return_value=[]))
            battle._records['bot:%d' % index] = {
                'engine_id': engine_id, 'network_id': index, 'kind': 'bot',
                'local': False, 'ready': True,
                'state': {'health': 100, 'alive': True}}
        battle._server_entity = lambda entity_id: entities.get(entity_id)
        for shot_seq in range(1, 30):
            event = dict(_event())
            event.update({
                'projectile_id': 'player:7:%d' % shot_seq,
                'shot_seq': shot_seq,
                'gravity': 190.0,
                'maxDistance': 100000.0,
            })
            self.assertTrue(battle._accept_projectile_event(event))

        bigworld.now = 1.0
        totals = {'chords': 0, 'scans': 0}
        invocations = 0
        while True:
            self.assertTrue(battle._advance_projectiles(1.0))
            perf = battle._projectile_perf
            totals['chords'] += perf['chords']
            totals['scans'] += perf['scans']
            invocations += 1
            if perf['debt'] <= 1e-9:
                break
            self.assertLess(invocations, 11)

        self.assertEqual(11, invocations)
        self.assertEqual(638, totals['chords'])
        self.assertEqual(19140, totals['scans'])
        self.assertEqual(58, battle._projectile_perf['chords'])
        self.assertEqual(1740, battle._projectile_perf['scans'])
        self.assertEqual(0, battle._projectile_perf['candidates'])
        for entity_id, entity in entities.items():
            if entity_id != 41:
                entity.collideSegmentExt.assert_not_called()

    def test_budgeted_catchup_uses_historic_target_pose_for_relative_sweep(self):
        battle, bigworld = _battle()
        target = types.SimpleNamespace(
            id=42, isStarted=True, position=_Vector((10.0, 0.0, 0.0)),
            isAlive=lambda: True)
        observed = []

        def collide(start, end):
            observed.append((tuple(start), tuple(end)))
            return []

        target.collideSegmentExt = collide
        battle._records['bot:8'] = {
            'engine_id': 42, 'network_id': 8, 'kind': 'bot',
            'local': False, 'ready': True,
            'state': {'health': 100, 'alive': True}}
        source = battle._server_entity(41)
        battle._server_entity = lambda entity_id: (
            source if entity_id == 41 else target if entity_id == 42 else None)
        battle._projectile_record_positions = mock.Mock(side_effect=[
            {'player:7': (0.0, 0.0, 0.0), 'bot:8': (10.0, 0.0, 0.0)},
            {'player:7': (0.0, 0.0, 0.0), 'bot:8': (20.0, 0.0, 0.0)},
        ])
        self.assertTrue(battle._accept_projectile_event(_event()))
        module = sys.modules[BattleRuntime.__module__]
        old_budget = module.PROJECTILE_CHORDS_PER_FRAME
        old_maximum = module.PROJECTILE_MAX_CHORDS_PER_FRAME
        module.PROJECTILE_CHORDS_PER_FRAME = 1
        module.PROJECTILE_MAX_CHORDS_PER_FRAME = 1
        try:
            bigworld.now = 1.0
            battle._advance_projectiles(1.0)
            bigworld.now = 1.1
            battle._advance_projectiles(1.1)
        finally:
            module.PROJECTILE_CHORDS_PER_FRAME = old_budget
            module.PROJECTILE_MAX_CHORDS_PER_FRAME = old_maximum

        self.assertGreaterEqual(len(observed), 2)
        # The second delayed chord still belongs near launch time.  Its query
        # must compensate from that historic pose into the target's current
        # collision matrix instead of pretending the target stood still.
        self.assertGreater(observed[-1][0][0], 9.0)

    def test_snapshot_restores_only_after_checked_cursor(self):
        battle, bigworld = _battle(now=10.0)
        battle.client.authority_epoch = 2
        snapshot = {
            'server_time_ms': 10000, 'authority_epoch': 2,
            'projectile_revision': 4,
            'projectiles': [dict(
                _event(), max_distance=100.0,
                launch_server_time_ms=8000,
                checked_through_ms=1000, checked_distance=10.0,
                piercing_loss=3.0)]}
        snapshot['projectiles'][0].pop('maxDistance')
        snapshot['projectiles'][0].pop('kind')
        snapshot['projectiles'][0].pop('attacker')
        snapshot['projectiles'][0].pop('authority_epoch')
        snapshot['projectiles'][0]['authority_epoch'] = 2

        battle._observe_projectile_message(snapshot)
        self.assertTrue(battle._reconcile_projectile_snapshot(snapshot))
        state = battle._projectiles.get('player:7:1')
        self.assertEqual(1.0, state['elapsed'])
        self.assertEqual(10.0, state['distance'])
        self.assertEqual(9.0, state['cursor_time'])
        self.assertEqual(3.0, battle._projectile_meta[
            'player:7:1']['piercing_loss'])

    def test_takeover_restores_disconnected_shooter_from_frozen_vehicle(self):
        battle, bigworld = _battle(now=10.0)
        descriptor = battle._server_entity(41).typeDescriptor
        battle._records = {}
        battle._server_entity = lambda unused_entity_id: None
        battle._resolve_descriptor = lambda vehicle: (
            descriptor if vehicle == 'ussr:R11_MS-1' else None)
        battle.client.authority_epoch = 2
        snapshot = {
            'server_time_ms': 10000, 'authority_epoch': 2,
            'projectile_revision': 4,
            'projectiles': [dict(
                _event(), max_distance=100.0,
                launch_server_time_ms=10000,
                checked_through_ms=0, checked_distance=0.0,
                piercing_loss=0.0, authority_epoch=2)]}
        snapshot['projectiles'][0].pop('maxDistance')
        snapshot['projectiles'][0].pop('kind')
        snapshot['projectiles'][0].pop('attacker')

        battle._observe_projectile_message(snapshot)
        self.assertTrue(battle._reconcile_projectile_snapshot(snapshot))
        self.assertTrue(battle._projectiles.contains('player:7:1'))
        self.assertIs(descriptor, battle._projectile_meta[
            'player:7:1']['source_descriptor'])

        bigworld.wall_x = 5.0
        bigworld.now = 10.6
        battle._next_projectile_progress_time = 99.0
        self.assertTrue(battle._advance_projectiles(10.6))
        self.assertEqual('impact', battle.client.resolutions[0][0][3])

    def test_destructible_receipt_retries_with_same_progress_until_ack(self):
        battle, bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        receipt = {
            'destructible_kind': 'fragile', 'chunk_id': 7,
            'item_index': 3, 'x': 1.0, 'y': 0.5, 'z': 0.0,
            'fall_yaw': 0.2, 'speed': 12.0, 'is_shot': True,
        }
        battle._projectile_destructible_context = 'player:7:1'
        try:
            self.assertTrue(battle._report_destructible(receipt))
        finally:
            battle._projectile_destructible_context = None

        bigworld.now = 0.1
        battle._projectiles.advance(
            0.1, lambda *_unused: None, lambda *_unused: None)
        self.assertTrue(battle._publish_projectile_progress())
        first = battle.client.progress[-1][1][0]
        self.assertEqual([receipt], first['destructibles'])
        meta = battle._projectile_meta['player:7:1']
        self.assertEqual([], meta['destructibles_pending'])
        self.assertEqual(first, meta['progress_pending'])

        # Enqueue success is not an acknowledgement. A lost socket write or
        # authority handoff must retry the exact same cursor and receipt.
        self.assertTrue(battle._publish_projectile_progress())
        self.assertEqual(first, battle.client.progress[-1][1][0])

        acknowledged = dict(_event())
        acknowledged['max_distance'] = acknowledged.pop('maxDistance')
        acknowledged.update({
            'checked_through_ms': first['checked_through_ms'],
            'checked_distance': first['checked_distance'],
            'piercing_loss': first['piercing_loss'],
        })
        battle._install_projectile_meta(
            battle._projectile_wire_meta(acknowledged))
        self.assertIsNone(meta['progress_pending'])

    def test_scene_carries_loss_and_uses_total_distance_for_falloff(self):
        battle, unused_bigworld = _battle()

        class _Destructibles(object):
            def __init__(self):
                self.calls = 0

            def shot_world_distance(self, *unused_args):
                self.calls += 1
                if self.calls == 1:
                    return {
                        'piercing_loss': 5.0,
                        'loss_distance': 0.5,
                        'continue_from': 1.0,
                    }
                return {
                    'piercing_loss': 0.0,
                    'world_distance': 999999.0,
                }

        battle._destructibles = _Destructibles()
        start = _Vector((0.0, 0.0, 0.0))
        end = _Vector((2.0, 0.0, 0.0))
        direction = _Vector((1.0, 0.0, 0.0))
        with mock.patch.object(
                combat_rules, 'sampled_piercing', return_value=100.0) as sampled:
            scene = battle._resolve_shot_scene(
                start, end, direction, {}, penetration_factor=1.0,
                initial_piercing_loss=2.0, distance_offset=10.0)

        self.assertEqual(7.0, scene['piercing_loss'])
        self.assertEqual(10.5, sampled.call_args[0][1])
        self.assertEqual(7.0, sampled.call_args[0][3])

    def test_nonimpact_resolution_sends_no_impact_position(self):
        battle, unused_bigworld = _battle()
        meta = battle._projectile_wire_meta(_event())
        meta['pending_resolution'] = {
            'state': {'elapsed': 10.0, 'distance': 100.0},
            'outcome': 'miss', 'impact': (100.0, 0.0, 0.0),
            'direct': None, 'splash': [],
        }
        battle._projectile_meta[meta['projectile_id']] = meta

        self.assertTrue(battle._submit_projectile_resolution(meta))
        args, unused_kwargs = battle.client.resolutions[0]
        self.assertEqual('miss', args[3])
        self.assertIsNone(args[5])

    def test_human_record_normalizes_to_player_wire_kind(self):
        battle, unused_bigworld = _battle()
        effect = battle._projectile_effect(
            {'kind': 'human', 'network_id': 9, 'state': {}},
            12, 2, (1.0, 2.0, 3.0), None, 12)
        self.assertEqual('player', effect['target_kind'])

    def test_terminal_event_retires_server_expired_projectile(self):
        battle, unused_bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))
        self.assertTrue(battle._projectiles.contains('player:7:1'))

        event = {
            'kind': 'projectile_impact', 'projectile_id': 'player:7:1'}
        battle._prepare_ordered_event(event)
        self.assertTrue(battle._apply_projectile_terminal_event(event))
        self.assertFalse(battle._projectiles.contains('player:7:1'))
        self.assertNotIn('player:7:1', battle._projectile_meta)

    def test_progress_holds_exact_cas_until_snapshot_acknowledges_it(self):
        battle, bigworld = _battle()
        self.assertTrue(battle._accept_projectile_event(_event()))

        bigworld.now = 0.1
        battle._advance_projectiles(0.1)
        first = battle.client.progress[-1][1][0]
        self.assertEqual(0, first['base_checked_ms'])
        self.assertEqual(100, first['checked_through_ms'])

        bigworld.now = 0.2
        battle._advance_projectiles(0.2)
        second = battle.client.progress[-1][1][0]
        self.assertEqual(first, second)

        acknowledged = dict(_event())
        acknowledged['max_distance'] = acknowledged.pop('maxDistance')
        acknowledged.update({
            'checked_through_ms': 100,
            'checked_distance': first['checked_distance'],
            'piercing_loss': first['piercing_loss'],
        })
        battle._install_projectile_meta(
            battle._projectile_wire_meta(acknowledged))
        bigworld.now = 0.31
        battle._advance_projectiles(0.31)
        third = battle.client.progress[-1][1][0]
        self.assertEqual(100, third['base_checked_ms'])
        self.assertEqual(310, third['checked_through_ms'])

    def test_projectile_still_resolves_after_shooter_dies(self):
        battle, bigworld = _battle()
        source = battle._server_entity(41)
        self.assertTrue(battle._accept_projectile_event(_event()))
        source.isAlive = lambda: False
        bigworld.wall_x = 5.0
        battle._next_projectile_progress_time = 99.0

        bigworld.now = 0.6
        battle._advance_projectiles(0.6)
        self.assertEqual(1, len(battle.client.resolutions))
        self.assertEqual('impact', battle.client.resolutions[0][0][3])

    def test_launch_shell_index_stays_frozen_while_active_selection_changes(self):
        battle, unused_bigworld = _battle()
        source = battle._server_entity(41)
        first = source.typeDescriptor.gun.shots[0]
        second = types.SimpleNamespace(shell='second', maxDistance=50.0)
        source.typeDescriptor.gun.shots.append(second)
        source.typeDescriptor.activeGunShotIndex = 0
        self.assertTrue(battle._accept_projectile_event(_event()))
        source.typeDescriptor.activeGunShotIndex = 1

        meta = battle._projectile_meta['player:7:1']
        self.assertIs(first, battle._projectile_shot(meta))


if __name__ == '__main__':
    unittest.main()
