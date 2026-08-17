import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
sys.path.insert(0, str(PORT_ROOT / 'server'))
CLIENT_ROOT = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from lan_battle_server import (  # noqa: E402
    AUTHORITY_DESCRIPTOR_TIMEOUT_SECONDS,
    AUTHORITY_DESTRUCTIBLE_TIMEOUT_SECONDS,
    BattleState, CLIENT_BUILD_0922, Player, PROJECTILE_CAPABILITY,
)
from server_battle_authority import SERVER_AUTHORITY_ID  # noqa: E402
from gui.mods.offline_lan_0922 import descriptor_donation  # noqa: E402

from test_port_0922_server_authority import _projection  # noqa: E402


class _Socket(object):
    def __init__(self):
        self.lines = []

    def sendall(self, payload):
        for line in payload.decode('utf-8').splitlines():
            if line:
                self.lines.append(json.loads(line))

    def sent_kinds(self):
        return [line.get('type') for line in self.lines]


def _player(player_id, team=1, vehicle='ussr:R11_MS-1'):
    return Player(
        player_id, _Socket(), ('127.0.0.1', player_id),
        team=team, slot=max(0, player_id - 1), x=398.0, z=402.0,
        vehicle=vehicle, client_position=True, health=1000,
        max_health=1000, capabilities=(PROJECTILE_CAPABILITY,),
    )


def _catalog_rows():
    return [
        {'name': 'ussr:R11_MS-1', 'level': 1, 'tags': ['lightTank']},
        {'name': 'germany:G12_Ltraktor', 'level': 1,
         'tags': ['lightTank']},
        {'name': 'usa:T1_Cunningham', 'level': 1, 'tags': ['lightTank']},
    ]


def _state_with_catalog(clock=None):
    state = BattleState(map_name='01_karelia', clock=clock)
    state.client_build = CLIENT_BUILD_0922
    state.players[1] = _player(1)
    state._elect_room_host()
    self_ok = state.store_vehicle_catalog(
        1, {'type': 'descriptor_catalog', 'vehicles': _catalog_rows()})
    assert self_ok
    return state


def _bundle(state, projections=None, failures=None, complete=True):
    return {
        'type': 'descriptor_bundle',
        'round_id': state.round_id,
        'requested': list(state.descriptor_requested_names),
        'failures': list(failures or ()),
        'complete': complete,
        'projections': dict(projections or {}),
    }


def _donate_destructible_identity(state):
    world = state.server_authority.world
    rows = [
        [list(signature), 7 + index // 1000, index % 1000, None, None]
        for index, signature in enumerate(sorted(world._instances))
    ]
    return state.store_destructible_map(1, {
        'type': 'destructible_map', 'round_id': state.round_id,
        'map': state.map_name, 'part': 0, 'parts': 1,
        'unit_vehicle_mass': 8000.0, 'resources': {},
        'instances': rows,
    })


class DonationFlowTest(unittest.TestCase):
    def test_request_start_requests_missing_projections(self):
        state = _state_with_catalog()
        message, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertIsNotNone(state.server_authority)
        self.assertFalse(state.server_authority.started())
        self.assertEqual(SERVER_AUTHORITY_ID, state.bot_authority_id)
        requests = [line for line in state.players[1].conn.lines
                    if line.get('type') == 'descriptor_request']
        self.assertEqual(1, len(requests))
        self.assertEqual(state.round_id, requests[0]['round_id'])
        self.assertTrue(requests[0]['names'])
        self.assertEqual(sorted(state.pending_descriptor_names),
                         list(state.pending_descriptor_names))

    def test_bundle_completion_starts_the_authority(self):
        state = _state_with_catalog()
        state.request_start(1, '01_karelia')
        names = state.pending_descriptor_names
        projections = dict((name, _projection()) for name in names)
        result = state.donate_descriptors(1, _bundle(
            state, projections=projections))
        self.assertEqual('started', result)
        self.assertTrue(state.server_authority.started())
        self.assertTrue(state.bot_manifest)
        self.assertEqual(SERVER_AUTHORITY_ID,
                         state.bot_manifest_authority_id)
        for entry in state.bot_manifest:
            self.assertIn(entry['vehicle'], names)

    def test_partial_bundles_accumulate_before_start(self):
        state = _state_with_catalog()
        state.request_start(1, '01_karelia')
        names = list(state.pending_descriptor_names)
        first = {names[0]: _projection()}
        result = state.donate_descriptors(1, _bundle(
            state, projections=first, complete=False))
        self.assertIs(True, result)
        self.assertFalse(state.server_authority.started())
        rest = dict((name, _projection()) for name in names[1:])
        result = state.donate_descriptors(1, _bundle(
            state, projections=rest, complete=True))
        self.assertEqual('started', result)
        self.assertEqual('ready', _donate_destructible_identity(state))
        live = state.mark_battle_ready(
            1, {'type': 'battle_ready', 'round_id': state.round_id})
        self.assertIsNotNone(live)
        self.assertEqual('battle', state.phase)

    def test_non_host_donation_is_rejected(self):
        state = _state_with_catalog()
        state.players[2] = _player(2, team=2)
        state.request_start(1, '01_karelia')
        names = state.pending_descriptor_names
        result = state.donate_descriptors(2, _bundle(
            state, projections={names[0]: _projection()}))
        self.assertFalse(result)

    def test_unsolicited_projection_names_are_rejected(self):
        state = _state_with_catalog()
        state.request_start(1, '01_karelia')
        result = state.donate_descriptors(1, _bundle(
            state, projections={'france:not_requested': _projection()}))
        self.assertFalse(result)

    def test_donor_leaving_fails_the_round(self):
        state = _state_with_catalog()
        state.players[2] = _player(2, team=2)
        state.request_start(1, '01_karelia')
        started_round = state.round_id
        self.assertEqual(SERVER_AUTHORITY_ID, state.bot_authority_id)
        state.remove_player(1)
        self.assertIsNone(state.server_authority)
        self.assertEqual('waiting', state.phase)
        self.assertGreater(state.round_id, started_round)
        self.assertEqual('failed', state.authority_status)
        self.assertEqual('descriptor_donor_disconnected',
                         state.authority_fallback_reason)

    def test_lineup_uses_donated_catalog_tier_band(self):
        state = _state_with_catalog()
        state.request_start(1, '01_karelia')
        for name in state.pending_descriptor_names:
            self.assertIn(name, [row['name'] for row in _catalog_rows()])

    def test_empty_completion_fails_the_round(self):
        state = _state_with_catalog()
        start, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertEqual('server_pending', start['authority_status'])
        started_round = state.round_id

        result = state.donate_descriptors(1, _bundle(state))

        self.assertEqual('failed', result)
        self.assertEqual('waiting', state.phase)
        self.assertGreater(state.round_id, started_round)
        self.assertEqual('failed', state.authority_status)
        self.assertEqual('descriptor_projection_failed',
                         state.authority_fallback_reason)

    def test_explicit_projection_failure_fails_the_round(self):
        state = _state_with_catalog()
        state.request_start(1, '01_karelia')
        names = list(state.descriptor_requested_names)
        failed = names[-1]
        projections = dict(
            (name, _projection()) for name in names if name != failed)

        result = state.donate_descriptors(1, _bundle(
            state, projections=projections, failures=[failed]))

        self.assertEqual('failed', result)
        self.assertEqual('waiting', state.phase)
        roster = state.lobby_message()
        self.assertEqual('failed', roster['authority_status'])
        self.assertEqual('descriptor_projection_failed',
                         roster['authority_fallback_reason'])

    def test_no_descriptor_response_times_out_and_fails_the_round(self):
        now = [100.0]
        state = _state_with_catalog(clock=lambda: now[0])
        state.request_start(1, '01_karelia')
        now[0] += AUTHORITY_DESCRIPTOR_TIMEOUT_SECONDS + 0.01

        state.tick_once(1.0 / 30.0)

        self.assertEqual('failed', state.authority_status)
        self.assertEqual('waiting', state.phase)
        rosters = [line for line in state.players[1].conn.lines
                   if line.get('type') == 'roster']
        self.assertTrue(rosters)
        self.assertEqual('waiting', rosters[-1]['phase'])
        self.assertEqual('descriptor_timeout',
                         rosters[-1]['authority_fallback_reason'])

    def test_missing_requester_in_catalog_refuses_the_start(self):
        state = _state_with_catalog()
        state.vehicle_catalogs[1] = ({
            'name': 'germany:G12_Ltraktor', 'level': 1,
            'tags': ('lightTank',),
        },)

        start, error = state.request_start(1, '01_karelia')

        self.assertIsNone(start)
        self.assertEqual('lineup_unavailable', error)
        self.assertIsNone(state.server_authority)
        self.assertEqual('waiting', state.phase)
        self.assertEqual('failed', state.authority_status)
        self.assertEqual('lineup_unavailable',
                         state.authority_fallback_reason)

    def test_ready_waits_for_native_identities_then_reaches_live(self):
        state = _state_with_catalog()
        state.vehicle_catalogs[1] = tuple(_catalog_rows()[:1])
        state.descriptor_store.add('ussr:R11_MS-1', _projection())
        start, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertTrue(start['need_destructible_map'])
        self.assertTrue(state.server_authority.started())

        self.assertIsNone(state.mark_battle_ready(
            1, {'type': 'battle_ready', 'round_id': state.round_id}))
        self.assertEqual('loading', state.phase)
        self.assertEqual('ready', _donate_destructible_identity(state))
        live = state.activate_battle_if_ready()

        self.assertIsNotNone(live)
        self.assertEqual('server', live['authority_status'])
        self.assertEqual('battle', state.phase)

    def test_native_identity_timeout_fails_the_round(self):
        now = [10.0]
        state = _state_with_catalog(clock=lambda: now[0])
        state.vehicle_catalogs[1] = tuple(_catalog_rows()[:1])
        state.descriptor_store.add('ussr:R11_MS-1', _projection())
        state.request_start(1, '01_karelia')
        self.assertIsNone(state.mark_battle_ready(
            1, {'type': 'battle_ready', 'round_id': state.round_id}))
        now[0] += AUTHORITY_DESTRUCTIBLE_TIMEOUT_SECONDS + 0.01

        state.tick_once(1.0 / 30.0)

        self.assertEqual('failed', state.authority_status)
        self.assertEqual('waiting', state.phase)
        rosters = [line for line in state.players[1].conn.lines
                   if line.get('type') == 'roster']
        self.assertTrue(rosters)
        self.assertEqual('waiting', rosters[-1]['phase'])
        self.assertEqual('destructible_map_timeout',
                         rosters[-1]['authority_fallback_reason'])


class CatalogValidationTest(unittest.TestCase):
    def test_rejects_malformed_rows(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.players[1] = _player(1)
        self.assertFalse(state.store_vehicle_catalog(
            1, {'vehicles': [{'name': '', 'level': 1, 'tags': []}]}))
        self.assertFalse(state.store_vehicle_catalog(
            1, {'vehicles': [{'name': 'a:b', 'level': 99, 'tags': []}]}))
        self.assertFalse(state.store_vehicle_catalog(1, {'vehicles': []}))


class _Tester(object):
    def __init__(self, bbox):
        self.bbox = bbox


class ProjectionBuilderTest(unittest.TestCase):
    def _descriptor(self):
        gun = types.SimpleNamespace(
            shots=({'shell': {'kind': 'ARMOR_PIERCING', 'caliber': 45.0,
                              'damage': (110.0, 110.0)},
                    'speed': 700.0, 'gravity': 9.81,
                    'maxDistance': 720.0,
                    'piercingPower': (80.0, 60.0)},),
            reloadTime=2.3, clip=(1, 0.0),
            turretYawLimits=(-3.14, 3.14),
            pitchLimits={'absolute': (-0.35, 0.15)},
            rotationSpeed=0.7, shotDispersionAngle=0.0046,
            maxHealth=54, maxRegenHealth=27)
        chassis = types.SimpleNamespace(
            hitTester=_Tester(((-1.5, -0.8, -3.5), (1.5, 0.8, 3.5), None)),
            hullPosition=(0.0, 0.6, 0.0), rotationSpeed=0.66,
            shotDispersionFactors=(0.14, 0.14),
            maxHealth=170, maxRegenHealth=130)
        hull = types.SimpleNamespace(
            hitTester=_Tester(((-1.7, -0.2, -3.5), (1.7, 1.4, 3.5), None)),
            turretPositions=((0.0, 1.0, 0.0),),
            primaryArmor=(18.0, 16.0, 16.0))
        vehicle_type = types.SimpleNamespace(
            name='ussr:R11_MS-1', level=1, tags=('lightTank',))
        return types.SimpleNamespace(
            type=vehicle_type, maxHealth=1000, gun=gun,
            turret={'rotationSpeed': 0.7, 'circularVisionRadius': 445.0},
            physics={'weight': 8000.0, 'speedLimits': (9.4, 4.0)},
            chassis=chassis, hull=hull,
            engine={'maxHealth': 100, 'maxRegenHealth': 50})

    def test_projection_round_trips_through_json(self):
        projection = descriptor_donation.project_descriptor(
            self._descriptor())
        encoded = json.dumps(projection)
        decoded = json.loads(encoded)
        self.assertEqual('ussr:R11_MS-1', decoded['name'])
        self.assertEqual(1, decoded['level'])
        self.assertEqual(['lightTank'], decoded['tags'])
        self.assertEqual(1000, decoded['maxHealth'])
        shot = decoded['gun']['shots'][0]
        self.assertEqual(700.0, shot['speed'])
        self.assertEqual('ARMOR_PIERCING', shot['shell']['kind'])
        self.assertEqual([[-1.7, -0.2, -3.5], [1.7, 1.4, 3.5], None],
                         decoded['hull']['hitTester']['bbox'])
        self.assertEqual([18.0, 16.0, 16.0],
                         decoded['hull']['primaryArmor'])
        self.assertEqual(100.0, decoded['engine']['maxHealth'])

    def test_missing_hull_bbox_fails_closed(self):
        descriptor = self._descriptor()
        descriptor.hull = types.SimpleNamespace(turretPositions=())
        with self.assertRaises(ValueError):
            descriptor_donation.project_descriptor(descriptor)

    def test_vehicle_catalog_reads_the_runtime_list(self):
        entries = {
            0: {1: types.SimpleNamespace(
                name='ussr:R11_MS-1', level=1, tags=('lightTank',))},
            1: {7: types.SimpleNamespace(
                name='germany:G12_Ltraktor', level=1,
                tags=('lightTank',))},
        }
        runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=('ussr', 'germany'),
                INDICES={'ussr': 0, 'germany': 1}),
            vehicles=types.SimpleNamespace(
                g_list=types.SimpleNamespace(
                    getList=lambda nation_id: entries[nation_id])))
        rows = descriptor_donation.vehicle_catalog(runtime)
        self.assertEqual(
            ['germany:G12_Ltraktor', 'ussr:R11_MS-1'],
            [row['name'] for row in rows])
        self.assertEqual(['lightTank'], rows[0]['tags'])

    def test_project_vehicles_reports_each_projection_failure(self):
        descriptor = self._descriptor()

        def resolve(typeName=None):
            if typeName == 'test:good':
                return descriptor
            raise ValueError('missing descriptor')

        runtime = types.SimpleNamespace(
            vehicles=types.SimpleNamespace(VehicleDescr=resolve))
        failures = []

        projections = descriptor_donation.project_vehicles(
            runtime, ['test:good', 'test:bad'], failures=failures)

        self.assertEqual(['test:good'], sorted(projections))
        self.assertEqual(['test:bad'], failures)


if __name__ == '__main__':
    unittest.main()
