import sys
import types
import unittest
from pathlib import Path

PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, PREBATTLE_SECONDS, TICK_HZ,
)
import server_battle_authority  # noqa: E402
from server_battle_authority import (  # noqa: E402
    SERVER_AUTHORITY_ID, ServerBattleAuthority,
)
import server_world  # noqa: E402
from descriptor_projection import DescriptorStore, wrap  # noqa: E402

from test_port_0922_server_authority import (  # noqa: E402
    _projection, _state_with_authority,
)
from test_port_0922_server_world import _graph  # noqa: E402


_IDENTITY_BASIS = (1000, 0, 0, 0, 1000, 0, 0, 0, 1000)


def _signature(x, y, z):
    return (int(round(x * 1000)), int(round(y * 1000)),
            int(round(z * 1000))) + _IDENTITY_BASIS


def _catalog(fence_at=(24.0, 0.0, 24.0), tree_at=None, column_at=None,
             wall_at=None):
    resources = {}
    instances = []
    if fence_at is not None:
        resources['env/fence.model'] = {
            'kind': 'fragile',
            'boxes': [[-2.0, 0.0, -0.3, 2.0, 1.6, 0.3, None]],
        }
        instances.append(
            list(_signature(*fence_at)) + ['env/fence.model', 0])
    if tree_at is not None:
        resources['env/tree.model'] = {
            'kind': 'falling',
            'boxes': [[-0.4, 0.0, -0.4, 0.4, 7.0, 0.4, None]],
        }
        instances.append(
            list(_signature(*tree_at)) + ['env/tree.model', 0])
    if column_at is not None:
        resources['env/column.model'] = {
            'kind': 'falling',
            'boxes': [[-0.6, 0.0, -0.6, 0.6, 5.0, 0.6, None]],
        }
        instances.append(
            list(_signature(*column_at)) + ['env/column.model', 0])
    if wall_at is not None:
        resources['env/wall.model'] = {
            'kind': 'structure',
            'boxes': [[-3.0, 0.0, -0.5, 3.0, 4.0, 0.5, 73]],
        }
        instances.append(
            list(_signature(*wall_at)) + ['env/wall.model', None])
    return {
        'format': 'x', 'version': 3, 'game_version': 'x',
        'map': '01_karelia', 'locator_quantization': 1000,
        'resources': resources, 'instances': instances,
        'ambiguous_instances': [], 'census': {},
    }


def _donation(catalog, health=10.0, correction=1.0, unit_mass=8000.0,
              wall_module_health=200.0):
    rows = []
    resources = {}
    next_item = 0
    for row in catalog['instances']:
        signature = row[:12]
        filename = row[12]
        kind = catalog['resources'][filename]['kind']
        if kind == 'structure':
            scaled = None
            modules = {'73': [wall_module_health, 40.0]}
            destr_type = 'structure'
        else:
            scaled = health
            modules = None
            destr_type = ('column' if 'column' in filename else
                          'tree' if 'tree' in filename else 'fragile')
        resources[filename] = {
            'destr_type': destr_type,
            'kinetic_correction': correction,
        }
        rows.append([list(signature), 7, next_item, scaled, modules])
        next_item += 1
    return rows, resources, unit_mass


def _world(**kwargs):
    catalog = _catalog(**kwargs)
    world = server_world.BakedWorld(
        _graph(width=16, height=16), catalog=catalog)
    return world, catalog


def _installed_world(**kwargs):
    donation_kwargs = {}
    for name in ('health', 'correction', 'unit_mass',
                 'wall_module_health'):
        if name in kwargs:
            donation_kwargs[name] = kwargs.pop(name)
    world, catalog = _world(**kwargs)
    rows, resources, unit_mass = _donation(catalog, **donation_kwargs)
    world.install_destructible_map(rows, resources, unit_mass)
    return world


class OcclusionTest(unittest.TestCase):
    def test_live_fence_blocks_sight_until_destroyed(self):
        world = _installed_world(fence_at=(24.0, 0.0, 24.0))
        start = (24.0, 0.8, 4.0)
        end = (24.0, 0.8, 44.0)
        self.assertIsNotNone(world.segment_hit(start, end))
        world.mark_destroyed(_signature(24.0, 0.0, 24.0))
        self.assertIsNone(world.segment_hit(start, end))

    def test_destroyed_column_keeps_blocking(self):
        world = _installed_world(fence_at=None,
                                 column_at=(24.0, 0.0, 24.0))
        world.mark_destroyed(_signature(24.0, 0.0, 24.0))
        self.assertIsNotNone(world.segment_hit(
            (24.0, 1.0, 4.0), (24.0, 1.0, 44.0)))

    def test_destroyed_wall_module_opens_the_wall(self):
        world = _installed_world(fence_at=None, wall_at=(24.0, 0.0, 24.0))
        start = (24.0, 1.0, 4.0)
        end = (24.0, 1.0, 44.0)
        self.assertIsNotNone(world.segment_hit(start, end))
        world.mark_destroyed(_signature(24.0, 0.0, 24.0), 73)
        self.assertIsNone(world.segment_hit(start, end))

    def test_wire_identity_marks_the_ledger(self):
        world = _installed_world(fence_at=(24.0, 0.0, 24.0))
        self.assertTrue(world.mark_destroyed_wire(7, 0))
        self.assertTrue(world.is_destroyed(_signature(24.0, 0.0, 24.0)))


class CrushLawTest(unittest.TestCase):
    def test_kinetic_law_matches_the_retail_numbers(self):
        world = _installed_world(fence_at=(24.0, 0.0, 24.0), health=10.0,
                                 correction=1.0, unit_mass=8000.0)
        instance = world.instance(_signature(24.0, 0.0, 24.0))
        self.assertTrue(world.crushable(instance, None, 8000.0, 5.0))
        self.assertFalse(world.crushable(instance, None, 8000.0, 2.0))

    def test_structure_module_ignores_mass_ratio(self):
        world = _installed_world(fence_at=None, wall_at=(24.0, 0.0, 24.0),
                                 wall_module_health=50.0)
        instance = world.instance(_signature(24.0, 0.0, 24.0))
        self.assertFalse(world.crushable(instance, 73, 8000.0, 5.0))
        self.assertTrue(world.crushable(instance, 73, 30000.0, 6.0))


class _FakeBots(object):
    def __init__(self, descriptors):
        self._descriptors = descriptors
    motion_world_receipt_reusable = None


def _battle_state():
    state = BattleState(map_name='01_karelia',
                        authority_mode='server')
    state.client_build = CLIENT_BUILD_0922
    state.phase = 'battle'
    state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ)) + 1
    state.roster_finalized = True
    return state


def _driver(world):
    state = _battle_state()
    store = DescriptorStore({'ussr:R11_MS-1': _projection()})
    driver = ServerBattleAuthority(state, world, store)
    driver._round_id = state.round_id
    driver._bots = _FakeBots({9: store.get('ussr:R11_MS-1')})
    state.server_authority = driver
    state.bot_authority_id = SERVER_AUTHORITY_ID
    return driver, state


class MotionCrushTest(unittest.TestCase):
    def test_bot_crushes_a_fence_and_reports_the_event(self):
        world = _installed_world(fence_at=(24.0, 0.0, 24.0))
        driver, state = _driver(world)
        descriptor = driver.descriptors.get('ussr:R11_MS-1')
        status = driver._resolve_motion(
            9, (24.0, 0.0, 20.5), 0.0, 6.0, descriptor, 1.0 / 30.0, 1.0)
        self.assertEqual('crushed', status)
        self.assertIn(('fragile', 7, 0, None), state.destructibles)
        self.assertTrue(world.is_destroyed(_signature(24.0, 0.0, 24.0)))
        again = driver._resolve_motion(
            9, (24.0, 0.0, 20.5), 0.0, 6.0, descriptor, 1.0 / 30.0, 1.1)
        self.assertEqual('clear', again)

    def test_slow_bot_holds_instead_of_crushing(self):
        world = _installed_world(fence_at=(24.0, 0.0, 24.0), health=40.0)
        driver, unused_state = _driver(world)
        descriptor = driver.descriptors.get('ussr:R11_MS-1')
        status = driver._resolve_motion(
            9, (24.0, 0.0, 20.5), 0.0, 2.0, descriptor, 1.0 / 30.0, 1.0)
        self.assertIn(status, ('soft', 'hard'))

    def test_crushed_column_still_blocks_this_tick(self):
        world = _installed_world(fence_at=None,
                                 column_at=(24.0, 0.0, 22.5),
                                 health=1.0)
        driver, state = _driver(world)
        descriptor = driver.descriptors.get('ussr:R11_MS-1')
        status = driver._resolve_motion(
            9, (24.0, 0.0, 20.5), 0.0, 8.0, descriptor, 1.0 / 30.0, 1.0)
        self.assertEqual('hard', status)
        self.assertIn(('column', 7, 0, None), state.destructibles)


class ShotTraversalTest(unittest.TestCase):
    def _driver_with_shell(self, kind, **world_kwargs):
        world = _installed_world(**world_kwargs)
        driver, state = _driver(world)
        projection = _projection()
        projection['gun']['shots'][0]['shell']['kind'] = kind
        driver._bots = _FakeBots({9: wrap(projection)})
        return driver, state, world

    @staticmethod
    def _meta(driver):
        shot = driver._bots._descriptors[9].gun.shots[0]
        return {
            'shooter_kind': 'bot', 'shooter_id': 9, 'shot_seq': 1,
            'shell_index': 0, 'penetration_factor': 1.0,
            'source_shot':
                server_battle_authority._source_shot_from_descriptor(shot),
        }

    def test_ap_shell_pierces_a_fence_with_fixed_loss(self):
        driver, state, world = self._driver_with_shell(
            'ARMOR_PIERCING', fence_at=(24.0, 0.0, 24.0), health=10.0)
        meta = self._meta(driver)
        stop = driver._traverse_shot_destructibles(
            meta, {'distance': 0.0}, (24.0, 0.8, 4.0), (24.0, 0.8, 44.0),
            1.0)
        self.assertIsNone(stop)
        wire_id = driver._wire_projectile_id(meta)
        self.assertEqual(25.0, driver._piercing_loss[wire_id])
        receipts = driver._shot_receipts[wire_id]
        self.assertEqual(1, len(receipts))
        self.assertEqual('fragile', receipts[0]['destructible_kind'])
        self.assertTrue(receipts[0]['is_shot'])
        self.assertTrue(world.is_destroyed(_signature(24.0, 0.0, 24.0)))

    def test_he_shell_stops_at_the_fence_but_destroys_it(self):
        driver, unused_state, world = self._driver_with_shell(
            'HIGH_EXPLOSIVE', fence_at=(24.0, 0.0, 24.0), health=10.0)
        meta = self._meta(driver)
        stop = driver._traverse_shot_destructibles(
            meta, {'distance': 0.0}, (24.0, 0.8, 4.0), (24.0, 0.8, 44.0),
            1.0)
        self.assertIsNotNone(stop)
        self.assertTrue(stop['world'])
        self.assertTrue(world.is_destroyed(_signature(24.0, 0.0, 24.0)))

    def test_apcr_shell_pierces_the_same_eligible_fence(self):
        driver, unused_state, unused_world = self._driver_with_shell(
            'ARMOR_PIERCING_CR', fence_at=(24.0, 0.0, 24.0), health=19.0)
        meta = self._meta(driver)
        stop = driver._traverse_shot_destructibles(
            meta, {'distance': 0.0}, (24.0, 0.8, 4.0), (24.0, 0.8, 44.0),
            1.0)
        self.assertIsNone(stop)
        self.assertEqual(
            25.0, driver._piercing_loss[driver._wire_projectile_id(meta)])

    def test_heat_shell_stops_at_the_first_fence(self):
        driver, unused_state, world = self._driver_with_shell(
            'HOLLOW_CHARGE', fence_at=(24.0, 0.0, 24.0), health=1.0)
        meta = self._meta(driver)
        stop = driver._traverse_shot_destructibles(
            meta, {'distance': 0.0}, (24.0, 0.8, 4.0), (24.0, 0.8, 44.0),
            1.0)
        self.assertEqual('destructible', stop['hit_kind'])
        self.assertTrue(world.is_destroyed(_signature(24.0, 0.0, 24.0)))

    def test_tough_destructible_stops_ap_shells_too(self):
        driver, unused_state, unused_world = self._driver_with_shell(
            'ARMOR_PIERCING', fence_at=(24.0, 0.0, 24.0), health=120.0)
        meta = self._meta(driver)
        stop = driver._traverse_shot_destructibles(
            meta, {'distance': 0.0}, (24.0, 0.8, 4.0), (24.0, 0.8, 44.0),
            1.0)
        self.assertIsNotNone(stop)


class DonationInstallTest(unittest.TestCase):
    def test_partial_parts_accumulate_then_incomplete_map_disables_feature(self):
        state = _state_with_authority(ready_world=False)
        state.request_start(1, '01_karelia')
        started_round = state.round_id
        authority = state.server_authority
        world = state.server_authority.world
        catalog_rows = [
            [list(_signature(1.0, 0.0, 1.0)), 3, 0, 5.0, None],
        ]
        first = state.store_destructible_map(1, {
            'type': 'destructible_map', 'round_id': state.round_id,
            'map': '01_karelia', 'part': 0, 'parts': 2,
            'unit_vehicle_mass': 8000.0,
            'resources': {'env/x.model': {
                'destr_type': 'fragile', 'kinetic_correction': 1.0}},
            'instances': catalog_rows,
        })
        self.assertTrue(first)
        self.assertFalse(world.has_destructible_identities())
        second = state.store_destructible_map(1, {
            'type': 'destructible_map', 'round_id': state.round_id,
            'map': '01_karelia', 'part': 1, 'parts': 2,
            'unit_vehicle_mass': 8000.0, 'resources': {},
            'instances': [],
        })
        self.assertEqual('ready', second)
        self.assertIs(state.server_authority, authority)
        self.assertEqual('loading', state.phase)
        self.assertEqual(started_round, state.round_id)
        self.assertEqual('server', state.authority_status)
        self.assertTrue(world.destructible_identities_ready())
        self.assertFalse(world.has_destructible_identities())
        self.assertEqual('destructible_map_incomplete',
                         world.destructibles_disabled_reason())
        self.assertNotIn('01_karelia', state.destructible_maps)

    def test_client_can_explicitly_disable_unavailable_destructibles(self):
        state = _state_with_authority(ready_world=False)
        start, error = state.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertTrue(start['need_destructible_map'])

        accepted = state.store_destructible_map(1, {
            'type': 'destructible_map', 'round_id': state.round_id,
            'map': '01_karelia', 'unavailable': True,
            'reason': 'client detail is log-only',
        })

        self.assertEqual('ready', accepted)
        self.assertEqual('server', state.authority_status)
        self.assertEqual(
            'client_destructible_map_unavailable',
            state.server_authority.world.destructibles_disabled_reason())

    def test_non_host_bundle_cannot_pollute_or_fallback_authority(self):
        state = _state_with_authority(ready_world=False)
        state.players[2] = __import__(
            'test_port_0922_server_authority')._player(2, team=2)
        state.request_start(1, '01_karelia')
        authority = state.server_authority
        signature = next(iter(authority.world._instances))

        accepted = state.store_destructible_map(2, {
            'type': 'destructible_map', 'round_id': state.round_id,
            'map': '01_karelia', 'part': 0, 'parts': 1,
            'unit_vehicle_mass': 8000.0, 'resources': {},
            'instances': [[list(signature), 7, 0, 5.0, None]],
        })

        self.assertFalse(accepted)
        self.assertIs(state.server_authority, authority)
        self.assertEqual('server_pending', state.authority_status)
        self.assertNotIn('01_karelia', state.destructible_maps)

    def test_pending_identity_donor_disconnect_disables_destructibles(self):
        state = _state_with_authority(ready_world=False)
        state.players[2] = __import__(
            'test_port_0922_server_authority')._player(2, team=2)
        state.request_start(1, '01_karelia')
        self.assertTrue(state.server_authority.started())
        signature = next(iter(state.server_authority.world._instances))
        self.assertTrue(state.store_destructible_map(1, {
            'type': 'destructible_map', 'round_id': state.round_id,
            'map': '01_karelia', 'part': 0, 'parts': 2,
            'unit_vehicle_mass': 8000.0, 'resources': {},
            'instances': [[list(signature), 7, 0, 5.0, None]],
        }))
        self.assertIn('01_karelia', state.destructible_maps)

        state.remove_player(1)

        self.assertIsNotNone(state.server_authority)
        self.assertNotIn('01_karelia', state.destructible_maps)
        self.assertEqual('loading', state.phase)
        self.assertEqual('server', state.authority_status)
        self.assertEqual('destructible_map_donor_disconnected',
                         state.server_authority.world.
                         destructibles_disabled_reason())

    def test_visible_destructible_report_cannot_mark_the_world(self):
        world = _installed_world(fence_at=(24.0, 0.0, 24.0))
        driver, state = _driver(world)
        state.players[2] = __import__(
            'test_port_0922_server_authority')._player(2)
        event = {
            'type': 'destructible', 'round_id': state.round_id,
            'destructible_kind': 'fragile', 'chunk_id': 7,
            'item_index': 0, 'x': 24.0, 'y': 0.0, 'z': 24.0,
            'fall_yaw': 0.0, 'speed': 5.0, 'is_shot': False,
        }

        self.assertFalse(state.report_destructible(2, event))
        self.assertFalse(world.is_destroyed(
            _signature(24.0, 0.0, 24.0)))
        self.assertTrue(state.report_destructible(
            SERVER_AUTHORITY_ID, event))
        self.assertTrue(world.is_destroyed(_signature(24.0, 0.0, 24.0)))

    def test_human_destructible_report_is_ignored_after_disable(self):
        world = _installed_world(fence_at=(24.0, 0.0, 24.0))
        unused_driver, state = _driver(world)
        state.players[2] = __import__(
            'test_port_0922_server_authority')._player(2)
        world.disable_destructibles('destructible_map_timeout')
        state_revision = state.state_revision

        accepted = state.report_destructible(2, {
            'type': 'destructible', 'round_id': state.round_id,
            'destructible_kind': 'fragile', 'chunk_id': 7,
            'item_index': 0, 'x': 24.0, 'y': 0.0, 'z': 24.0,
            'fall_yaw': 0.0, 'speed': 5.0, 'is_shot': False,
        })

        self.assertTrue(accepted)
        self.assertEqual({}, state.destructibles)
        self.assertEqual([], state.pending_events)
        self.assertEqual(0, state.destructible_revision)
        self.assertEqual(state_revision, state.state_revision)
        self.assertFalse(world.is_destroyed(
            _signature(24.0, 0.0, 24.0)))
        self.assertEqual(True, state._authority_fields()[
            'destructibles_disabled'])
        self.assertEqual(
            'destructible_map_timeout', state._authority_fields()[
                'destructibles_disabled_reason'])


if __name__ == '__main__':
    unittest.main()
