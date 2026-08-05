import inspect
import json
import pickle
import sys
from collections.abc import Mapping
from pathlib import Path
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.account_rpc import commands
from gui.mods.offline_lan_0922.account_rpc import data as account_data
from gui.mods.offline_lan_0922 import compat as compatibility
from gui.mods.offline_lan_0922.account_rpc.server import FakeServer
from gui.mods.offline_lan_0922.account_rpc.state import AccountState


CONTRACT_PATH = (
    ROOT / 'ports' / '0.9.22' / 'tools' /
    'account_lobby_consumer_contract.json')
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))


def _contract_path(values, path):
    """Resolve one producer path from the machine-readable lobby contract."""
    root, remainder = path.split('.', 1)
    value = values[root]
    for key in remainder.split('.'):
        value = value[key]
    return value


class _Player(object):
    def __init__(self):
        self.responses = []
        self.ext_responses = []
        self.streams = []

    def onCmdResponse(self, request_id, result_id, error):
        self.responses.append((request_id, result_id, error))

    def onStreamComplete(self, request_id, desc, payload):
        self.streams.append((request_id, desc, payload))

    def onCmdResponseExt(self, request_id, result_id, error, ext):
        self.ext_responses.append((request_id, result_id, error, ext))


class AccountRpcTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.player = _Player()
        self.server = FakeServer(lambda: self.player,
                                 lambda delay, fn: self.pending.append((delay, fn)))

    def _run(self):
        self.assertTrue(self.pending)
        delay, callback = self.pending.pop(0)
        self.assertEqual(0.0, delay)
        callback()

    def test_exact_registered_handler_is_asynchronous(self):
        self.server.doCmdInt3(31, commands.CMD_REQ_SERVER_STATS, 0, 0, 0)
        self.assertEqual([], self.player.responses)
        self._run()
        self.assertEqual([(31, commands.RES_SUCCESS, '')], self.player.responses)

    def test_response_is_dropped_after_account_is_retired(self):
        active = [self.player]
        server = FakeServer(
            lambda: active[0],
            lambda delay, fn: self.pending.append((delay, fn)))

        server.doCmdInt3(39, commands.CMD_REQ_SERVER_STATS, 0, 0, 0)
        active[0] = None
        self._run()

        self.assertEqual([], self.player.responses)

    def test_stream_is_dropped_if_account_changes_after_response(self):
        active = [self.player]
        server = FakeServer(
            lambda: active[0],
            lambda delay, fn: self.pending.append((delay, fn)))

        server.doCmdInt3(40, commands.CMD_SYNC_SHOP, 0, 0, 0)
        self._run()
        self.assertEqual([(40, commands.RES_STREAM, '')],
                         self.player.responses)
        active[0] = _Player()
        self._run()

        self.assertEqual([], self.player.streams)

    def test_server_stats_event_is_async_and_precedes_command_response(self):
        trace = []
        self.player.onCmdResponse = lambda *args: trace.append('response')
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'receive_server_stats': lambda value: trace.append('stats')})

        server.doCmdInt3(38, commands.CMD_REQ_SERVER_STATS, 0, 0, 0)
        self.assertEqual([], trace)
        self._run()
        self.assertEqual(['stats', 'response'], trace)
        self.assertTrue(
            CONTRACT['deliveryOrder']['allClientCallbacksAreAsynchronous'])
        self.assertTrue(
            CONTRACT['deliveryOrder']['serverStatsBeforeCommandResponse'])

    def test_unknown_command_returns_failure_not_success(self):
        self.server.doCmdInt3(32, 999999, 0, 0, 0)
        self._run()
        self.assertEqual(commands.RES_FAILURE, self.player.responses[0][1])
        self.assertEqual('UNSUPPORTED_OFFLINE_COMMAND', self.player.responses[0][2])

    def test_eula_version_survives_server_restart_and_can_be_deleted(self):
        eula_contract = CONTRACT['intUserSettings']
        self.assertEqual(
            eula_contract['addCommand'], commands.CMD_ADD_INT_USER_SETTINGS)
        self.assertEqual(
            eula_contract['deleteCommand'],
            commands.CMD_DEL_INT_USER_SETTINGS)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'account_state.json')
            first_state = AccountState(path)
            first_server = FakeServer(
                lambda: self.player,
                lambda delay, fn: self.pending.append((delay, fn)),
                {'account_state': first_state})
            eula_key = eula_contract['eulaVersionKey']

            first_server.doCmdIntArr(
                41, commands.CMD_ADD_INT_USER_SETTINGS, [eula_key, 17])
            self.assertEqual([], self.player.responses)
            self._run()
            self.assertEqual(
                (41, commands.RES_SUCCESS, ''), self.player.responses[-1])

            restarted_state = AccountState(path)
            restarted_server = FakeServer(
                lambda: self.player,
                lambda delay, fn: self.pending.append((delay, fn)),
                {'account_state': restarted_state})
            restarted_server.doCmdInt3(
                42, commands.CMD_SYNC_DATA, 0, 0, 0)
            self._run()
            synced = pickle.loads(self.player.ext_responses[-1][3])
            self.assertEqual({eula_key: 17}, synced['intUserSettings'])

            restarted_server.doCmdIntArr(
                43, commands.CMD_DEL_INT_USER_SETTINGS, [eula_key])
            self._run()
            self.assertEqual(
                (43, commands.RES_SUCCESS, ''), self.player.responses[-1])
            self.assertEqual({}, AccountState(path).snapshot())

    def test_malformed_integer_settings_fail_without_mutating_state(self):
        state = AccountState(path=None)
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'account_state': state})

        server.doCmdIntArr(
            44, commands.CMD_ADD_INT_USER_SETTINGS, [54, 17, 99])
        self._run()

        self.assertEqual(commands.RES_FAILURE, self.player.responses[-1][1])
        self.assertEqual({}, state.snapshot())

    def test_stream_response_has_crc_and_pickled_body(self):
        self.server.doCmdInt3(33, commands.CMD_SYNC_SHOP, 0, 0, 0)
        self._run()
        self.assertEqual([(33, commands.RES_STREAM, '')],
                         self.player.responses)
        self.assertTrue(
            CONTRACT['deliveryOrder']['streamCommandResponseBeforePayload'])
        self.assertEqual([], self.player.streams)
        self._run()
        request_id, desc, payload = self.player.streams[0]
        corrupted, original_length, packet_length, original_crc, crc = desc
        self.assertEqual(33, request_id)
        self.assertFalse(corrupted)
        self.assertEqual(len(payload), original_length)
        self.assertEqual(len(payload), packet_length)
        self.assertEqual(zlib.crc32(payload) & 0xffffffff, crc)
        self.assertEqual(crc, original_crc)
        shop = pickle.loads(zlib.decompress(payload))
        self.assertEqual(0.5, shop['sellPriceFactor'])
        shop_contract = CONTRACT['shop']
        self.assertTrue(set(shop_contract['directKeys']).issubset(shop))
        self.assertEqual(
            set(shop_contract['itemsDirectKeys']), set(shop['items']))
        self.assertEqual(
            set(shop_contract['goodiesDirectKeys']), set(shop['goodies']))
        self.assertEqual(
            set(shop_contract['itemsDirectKeys']),
            set(shop['defaults']['items']))
        self.assertEqual(
            set(shop_contract['goodiesDirectKeys']),
            set(shop['defaults']['goodies']))
        for key, arity in shop_contract['tupleArities'].items():
            self.assertEqual(arity, len(shop[key]), key)
        for key in shop_contract['currencyMappings']:
            self.assertIsInstance(shop[key], dict)
            self.assertEqual(0, shop[key].get('gold'))
            self.assertIsInstance(shop['defaults'][key], dict)
        ref_contract = shop_contract['refSystem']
        ref_system = shop['refSystem']
        self.assertEqual(set(ref_contract['directKeys']), set(ref_system))
        self.assertEqual(ref_contract['disabledDefaults'], ref_system)
        self.assertIs(type(ref_system['posByXPinTeam']), int)

    def test_sync_data_dispatches_only_its_registered_shape(self):
        self.server.doCmdInt3(34, commands.CMD_SYNC_DATA, 6, 0, 0)
        self._run()
        request_id, result_id, error, ext = self.player.ext_responses[0]
        self.assertEqual(34, request_id)
        self.assertEqual(commands.RES_SUCCESS, result_id)
        self.assertEqual('', error)
        data = pickle.loads(ext)
        self.assertEqual(7, data['rev'])
        self.assertEqual(set(range(1, 13)), set(data['inventory']))
        self.assertEqual({}, data['inventory'][1]['compDescr'])

    def test_sync_data_populates_all_exact_lobby_consumer_caches(self):
        self.server.doCmdInt3(37, commands.CMD_SYNC_DATA, 0, 0, 0)
        self._run()
        value = pickle.loads(self.player.ext_responses[0][3])

        sync_contract = CONTRACT['syncData']
        self.assertTrue(set(sync_contract['directKeys']).issubset(value))
        self.assertEqual({}, value['quests'])
        self.assertEqual({}, value['tokens'])
        self.assertEqual(
            set(sync_contract['groupLocksDirectKeys']),
            set(value['groupLocks']))
        self.assertEqual(
            set(sync_contract['accountDirectKeys']), set(value['account']))
        self.assertEqual(
            set(sync_contract['statsDirectKeys']), set(value['stats']))
        self.assertEqual(
            set(sync_contract['cacheDirectKeys']), set(value['cache']))
        play_limits = value['stats']['playLimits']
        self.assertEqual(
            sync_contract['playLimitsTupleArities'][0], len(play_limits))
        for period in play_limits:
            self.assertEqual(
                sync_contract['playLimitsTupleArities'][1], len(period))
        self.assertEqual((), value['badges'])

        personal_missions = value['potapovQuests']
        pm_contract = sync_contract['potapovQuests']
        self.assertEqual(
            set(pm_contract['directKeys']), set(personal_missions))
        self.assertEqual('', personal_missions['compDescr'])
        for quest_type in ('regular', 'training'):
            progress = personal_missions[quest_type]
            self.assertEqual(
                set(pm_contract['progressDirectKeys']), set(progress))
            self.assertEqual(0, progress['slots'])
            self.assertEqual([], progress['selected'])
            self.assertEqual({}, progress['lastIDs'])

    def test_selected_vehicle_uses_exact_vehicle_item_index(self):
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'selected_vehicle': {'id': 9, 'compDescr': b'compact'}})
        server.doCmdInt3(35, commands.CMD_SYNC_DATA, 0, 0, 0)
        self._run()
        data = pickle.loads(self.player.ext_responses[0][3])
        inventory_contract = CONTRACT['inventory']
        self.assertEqual(
            set(inventory_contract['itemTypeIndices']),
            set(data['inventory']))
        vehicle_data = data['inventory'][1]
        self.assertEqual(
            set(inventory_contract['vehicleDirectKeys']), set(vehicle_data))
        self.assertEqual(
            set(inventory_contract['tankmanDirectKeys']),
            set(data['inventory'][8]))
        self.assertEqual({9: b'compact'}, data['inventory'][1]['compDescr'])
        for key, arity in inventory_contract[
                'selectedVehicleTupleArities'].items():
            self.assertEqual(arity, len(vehicle_data[key][9]), key)
        for key in inventory_contract['selectedVehicleMappingValues']:
            self.assertIsInstance(vehicle_data[key][9], dict)
        self.assertEqual((0, 0), vehicle_data['repair'][9])
        self.assertEqual({}, data['inventory'][1]['shellsLayout'][9])
        self.assertEqual(
            ((86400, ''), (604800, '')), data['stats']['playLimits'])
        self.assertEqual({}, data['inventory'][6])

    def test_account_validator_receives_only_validatable_inventory_shapes(self):
        value = account_data.sync_data()
        inventory = value['inventory']
        validator = CONTRACT['accountValidator']

        for item_type in validator['emptyItemTypeIndices']:
            self.assertEqual({}, inventory[item_type], item_type)
        self.assertIsInstance(inventory[1]['compDescr'], Mapping)
        self.assertIsInstance(inventory[8]['compDescr'], Mapping)
        self.assertIsInstance(value['stats']['eliteVehicles'], set)
        self.assertEqual({}, inventory[1]['compDescr'])
        self.assertEqual({}, inventory[8]['compDescr'])

        bootstrap = (
            CLIENT_SCRIPTS / 'gui' / 'mods' / 'offline_lan_0922' /
            'bootstrap.py').read_text(encoding='utf-8')
        self.assertEqual(
            'VehicleDescr.makeCompactDescr',
            validator['selectedVehicleCompDescrProducer'])
        self.assertIn('descriptor.makeCompactDescr()', bootstrap)

    def test_entire_lobby_controller_chain_receives_safe_nested_shapes(self):
        values = {
            'syncData': account_data.sync_data(),
            'shop': account_data.shop(),
            'serverSettings': compatibility._SERVER_SETTINGS,
        }
        chain = CONTRACT['lobbyControllerChain']

        for path in chain['mappingPaths']:
            self.assertIsInstance(_contract_path(values, path), Mapping, path)
        for path in chain['numberPaths']:
            value = _contract_path(values, path)
            self.assertIsInstance(value, (int, float), path)
            self.assertNotIsInstance(value, bool, path)
        for path in chain['booleanPaths']:
            self.assertIs(type(_contract_path(values, path)), bool, path)
        for path, minimum in chain['minimumSequenceLengths'].items():
            self.assertGreaterEqual(
                len(_contract_path(values, path)), minimum, path)
        for path, arity in chain['tupleArities'].items():
            self.assertEqual(
                arity, len(_contract_path(values, path)), path)

        # Exact #1513 ShopRequester supplies disabled objects for these
        # optional keys when they are absent. Keep them absent instead of
        # publishing a second, unverified server-side schema.
        for key in chain['defaultedShopKeys']:
            self.assertNotIn(key, values['shop'])
        self.assertEqual({}, values['syncData']['newYear'])
        self.assertTrue(chain['newYearEmptySyncDataIsSupported'])
        self.assertEqual(
            set(chain['directServerSettingsKeys']), {'wallet'})

    def test_dossier_stream_matches_native_two_tuple_consumer(self):
        self.server.doCmdInt3(36, commands.CMD_SYNC_DOSSIERS, 4, 0, 0)
        self._run()
        self._run()
        value = pickle.loads(zlib.decompress(self.player.streams[0][2]))
        self.assertEqual(
            CONTRACT['dossiers']['streamTupleArity'], len(value))
        for change in value[1]:
            self.assertEqual(
                CONTRACT['dossiers']['changeTupleArity'], len(change))
        self.assertEqual((5, []), value)

    def test_old_chat_mailbox_does_not_echo_command_as_chat_action(self):
        events = []
        server = FakeServer(
            lambda: self.player,
            lambda delay, fn: self.pending.append((delay, fn)),
            {'receive_chat_action': events.append})

        self.assertTrue(server.chatCommandFromClient(
            41, 9, 0, -1, 0, '', ''))
        self.assertTrue(server.chatCommandFromClient(
            42, 10, 0, -1, 0, '', ''))
        self.assertFalse(
            CONTRACT['chatAction']['publishedByOfflineServer'])
        self.assertFalse(
            CONTRACT['chatAction']['commandIndexMayBeUsedAsActionIndex'])
        self.assertEqual([], events)
        self.assertEqual([], self.pending)

    def test_chat2_mailbox_is_present_and_safely_one_way(self):
        self.assertTrue(
            self.server.messenger_onActionByClient_chat2(1, 43, ()))
        self.assertEqual([], self.pending)

    def test_fake_server_mailbox_arities_match_exact_contract(self):
        for name, arity in CONTRACT[
                'mailboxAritiesExcludingSelf'].items():
            method = getattr(FakeServer, name)
            parameters = list(inspect.signature(method).parameters)
            self.assertEqual('self', parameters[0], name)
            self.assertEqual(arity, len(parameters) - 1, name)

    def test_initial_account_and_lobby_direct_keys_match_contract(self):
        settings_contract = CONTRACT['initialServerSettings']
        self.assertTrue(
            set(settings_contract['directKeys']).issubset(
                compatibility._SERVER_SETTINGS))
        self.assertTrue(
            set(settings_contract['rankedConfigDirectKeys']).issubset(
                compatibility._SERVER_SETTINGS['ranked_config']))
        self.assertEqual(
            settings_contract['elenSettings'],
            compatibility._SERVER_SETTINGS['elenSettings'])
        for key, arity in settings_contract['tupleArities'].items():
            self.assertEqual(
                arity, len(compatibility._SERVER_SETTINGS[key]), key)
        roaming_hosts = compatibility._SERVER_SETTINGS['roaming'][
            settings_contract['roamingHostsIndex']]
        self.assertIsInstance(roaming_hosts, list)
        for host in roaming_hosts:
            self.assertEqual(
                settings_contract['roamingHostTupleArity'], len(host))
        self.assertTrue(
            set(CONTRACT['lobbyGuiContext']['directKeys']).issubset(
                compatibility._LOBBY_GUI_CONTEXT))

    def test_contract_is_driven_by_current_producer_functions(self):
        sync_value = account_data.sync_data()
        shop_value = account_data.shop()
        dossier_value = account_data.dossiers()
        self.assertTrue(
            set(CONTRACT['syncData']['directKeys']).issubset(sync_value))
        self.assertTrue(
            set(CONTRACT['shop']['directKeys']).issubset(shop_value))
        self.assertEqual(
            CONTRACT['dossiers']['streamTupleArity'], len(dossier_value))


if __name__ == '__main__':
    unittest.main()
