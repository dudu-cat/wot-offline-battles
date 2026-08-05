#!/usr/bin/env python2
"""Audit the exact #1513 lobby startup consumers against local producers.

This is deliberately a bytecode consumer audit, not a source-name grep.  It
reads the pinned client's CPython 2.7 code objects, proves the hard dictionary
subscripts on the stock lobby startup path, and then checks the offline
producer values which feed those consumers.
"""

from __future__ import print_function

import argparse
import ast
import json
import marshal
import os
import sys
import types
import zipfile

import dis


PYC_MAGIC = '\x03\xf3\r\n'

HARD_SUBSCRIPTS = (
    {
        'member': 'scripts/client/Account.pyc',
        'function': '_AccountRepository.fileServerSettings',
        'key': 'file_server',
        'producerPath': 'serverSettings.file_server',
        'shape': 'mapping',
    },
    {
        'member': 'scripts/client/gui/server_events/events_helpers.pyc',
        'function': 'EventInfoModel._getDailyProgressResetTimeOffset',
        'key': 'regional_settings',
        'producerPath': 'serverSettings.regional_settings',
        'shape': 'mapping',
    },
    {
        'member': 'scripts/client/gui/shared/ClanCache.pyc',
        'function': '_ClanCache.getFileFromServer',
        'key': 'file_server',
        'producerPath': 'serverSettings.file_server',
        'shape': 'mapping',
    },
    {
        'member': 'scripts/client/BattleReplay.pyc',
        'function': 'BattleReplay.__onAccountBecomePlayer',
        'key': 'roaming',
        'producerPath': 'serverSettings.roaming',
        'shape': 'sequence[4]',
    },
    {
        'member': 'scripts/client/predefined_hosts.pyc',
        'function': '_PreDefinedHostList.roamingHosts',
        'key': 'roaming',
        'producerPath': 'serverSettings.roaming',
        'shape': 'sequence[4]',
        'followupIndices': [3],
    },
    {
        'member': 'scripts/client/gui/game_control/wallet.pyc',
        'function': 'WalletController.onLobbyStarted',
        'key': 'wallet',
        'producerPath': 'serverSettings.wallet',
        'shape': 'sequence[2]',
        'followupIndices': [0, 1],
    },
    {
        'member': 'scripts/client/account_helpers/ClientRanked.pyc',
        'function': 'ClientRanked.isEnabled',
        'key': 'ranked_config',
        'producerPath': 'serverSettings.ranked_config',
        'shape': 'mapping',
    },
    {
        'member': 'scripts/client/account_helpers/ClientRanked.pyc',
        'function': 'ClientRanked.getSeason',
        'key': 'ranked_config',
        'producerPath': 'serverSettings.ranked_config',
        'shape': 'mapping',
    },
    {
        'member': 'scripts/client/account_helpers/ClientRanked.pyc',
        'function': 'ClientRanked.getConfigs',
        'key': 'ranked_config',
        'producerPath': 'serverSettings.ranked_config',
        'shape': 'mapping',
    },
    {
        'member': 'scripts/common/ranked_common.pyc',
        'function': 'getRankedSeason',
        'key': 'isEnabled',
        'producerPath': 'serverSettings.ranked_config.isEnabled',
        'shape': 'bool',
    },
    {
        'member': (
            'scripts/client/gui/shared/utils/requesters/StatsRequester.pyc'),
        'function': 'StatsRequester.todayPlayHours',
        'key': 0,
        'producerPath': 'syncData.stats.dailyPlayHours',
        'shape': 'non-empty-sequence',
    },
    {
        'member': (
            'scripts/client/gui/shared/utils/requesters/StatsRequester.pyc'),
        'function': 'StatsRequester.getDailyTimeLimits',
        'key': 0,
        'producerPath': 'syncData.stats.playLimits',
        'shape': 'sequence[2][2]',
    },
    {
        'member': (
            'scripts/client/gui/shared/utils/requesters/StatsRequester.pyc'),
        'function': 'StatsRequester.getWeeklyTimeLimits',
        'key': 1,
        'producerPath': 'syncData.stats.playLimits',
        'shape': 'sequence[2][2]',
    },
    {
        'member': 'scripts/client/gui/game_control/RefSystem.pyc',
        'function': '_getRefSystemPeriods',
        'key': 'periods',
        'producerPath': 'shop.refSystem.periods',
        'shape': 'int',
    },
    {
        'member': 'scripts/client/gui/game_control/RefSystem.pyc',
        'function': '_getMaxReferralXPPool',
        'key': 'maxReferralXPPool',
        'producerPath': 'shop.refSystem.maxReferralXPPool',
        'shape': 'int',
    },
    {
        'member': 'scripts/client/gui/game_control/RefSystem.pyc',
        'function': '_getMaxNumberOfReferrals',
        'key': 'maxNumberOfReferrals',
        'producerPath': 'shop.refSystem.maxNumberOfReferrals',
        'shape': 'int',
    },
    {
        'member': 'scripts/client/gui/game_control/RefSystem.pyc',
        'function': 'RefSystem.__update',
        'key': 'posByXPinTeam',
        'producerPath': 'shop.refSystem.posByXPinTeam',
        'shape': 'int',
    },
)

# ServerSettings directly indexes these keys only after a membership test.
# They are useful audit evidence, but absence is accepted by the stock client.
GUARDED_SERVER_SETTINGS = (
    'roaming',
    'file_server',
    'regional_settings',
    'clanProfile',
    'spgRedesignFeatures',
    'strongholdSettings',
    'rankedBattles',
    'hallOfFame',
    'ranked_config',
)

# ShopRequester catches KeyError around these direct nested reads.  The local
# producer still supplies them so read-only lobby views remain deterministic.
FALLBACK_SHOP_KEYS = {
    'items': (
        'itemPrices',
        'notInShopItems',
        'vehiclesNotToBuy',
        'vehiclesRentPrices',
        'vehiclesToSellForGold',
        'vehicleSellPriceFactors',
    ),
    'goodies': ('prices', 'notInShop'),
}

SAFE_STATS_GETTERS = {
    'restrictions': 'StatsRequester.restrictions',
    'playLimits': 'StatsRequester.playLimits',
    'dailyPlayHours': 'StatsRequester.dailyPlayHours',
    'vehTypeLocks': 'StatsRequester.vehicleTypeLocks',
    'globalVehicleLocks': 'StatsRequester.globalVehicleLocks',
    'refSystem': 'StatsRequester.refSystem',
    'mayConsumeWalletResources': 'StatsRequester.mayConsumeWalletResources',
}

# Complete exact-build inventory of literal dictionary subscripts whose base
# is the raw Account ``serverSettings`` mapping.  The scanner below discovers
# these from bytecode data flow (direct attribute or a local alias), so a new
# unclassified raw consumer fails the build instead of waiting for python.log.
RAW_SERVER_SETTINGS_ACCESS = {
    ('scripts/client/Account.pyc',
     '_AccountRepository.fileServerSettings', 'file_server'): 'hard',
    ('scripts/client/account_helpers/ClientRanked.pyc',
     'ClientRanked.isEnabled', 'ranked_config'): 'hard',
    ('scripts/client/account_helpers/ClientRanked.pyc',
     'ClientRanked.getSeason', 'ranked_config'): 'hard',
    ('scripts/client/account_helpers/ClientRanked.pyc',
     'ClientRanked.getConfigs', 'ranked_config'): 'hard',
    ('scripts/client/BattleReplay.pyc',
     'BattleReplay.__onAccountBecomePlayer', 'roaming'): 'hard',
    ('scripts/client/BattleReplay.pyc',
     'BattleReplay.__onAccountBecomePlayer',
     'spgRedesignFeatures'): 'membership-guarded',
    ('scripts/client/gui/game_control/wallet.pyc',
     'WalletController.onLobbyStarted', 'wallet'): 'hard',
    ('scripts/client/gui/server_events/events_helpers.pyc',
     'EventInfoModel._getDailyProgressResetTimeOffset',
     'regional_settings'): 'hard',
    ('scripts/client/gui/shared/ClanCache.pyc',
     '_ClanCache.getFileFromServer', 'file_server'): 'hard',
    ('scripts/client/helpers/time_utils.pyc',
     '_TimeCorrector.serverRegionalTime',
     'regional_settings'): 'exception-fallback',
    ('scripts/client/helpers/time_utils.pyc',
     '_TimeCorrector.modifiedServerRegionalTime',
     'regional_settings'): 'exception-fallback',
    ('scripts/client/predefined_hosts.pyc',
     '_PreDefinedHostList.roamingHosts', 'roaming'): 'hard',
}


def _walk_code(code, path=()):
    current = path + (code.co_name,)
    if code.co_name != '<module>':
        yield ('.'.join(part for part in current if part != '<module>'), code)
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            for result in _walk_code(value, current):
                yield result


def _read_code(archive, member):
    try:
        payload = archive.read(member)
    except KeyError:
        raise ValueError('missing bytecode member: %s' % member)
    if payload[:4] != PYC_MAGIC:
        raise ValueError('%s is not CPython 2.7 bytecode' % member)
    return marshal.loads(payload[8:])


def _instructions(code):
    """Decode the subset of CPython 2.7 instructions needed by this audit."""
    bytecode = code.co_code
    result = []
    offset = 0
    extended_arg = 0
    line_by_offset = dict(dis.findlinestarts(code))
    line = code.co_firstlineno
    while offset < len(bytecode):
        instruction_offset = offset
        opcode = ord(bytecode[offset])
        offset += 1
        argument = None
        value = None
        if opcode >= dis.HAVE_ARGUMENT:
            argument = (ord(bytecode[offset]) |
                        (ord(bytecode[offset + 1]) << 8) |
                        extended_arg)
            offset += 2
            if opcode == dis.EXTENDED_ARG:
                extended_arg = argument << 16
                continue
            extended_arg = 0
            if opcode in dis.hasconst:
                value = code.co_consts[argument]
            elif opcode in dis.hasname:
                value = code.co_names[argument]
            elif opcode in dis.haslocal:
                value = code.co_varnames[argument]
            elif opcode in dis.hasfree:
                freevars = code.co_cellvars + code.co_freevars
                value = freevars[argument]
        if instruction_offset in line_by_offset:
            line = line_by_offset[instruction_offset]
        result.append({
            'offset': instruction_offset,
            'opname': dis.opname[opcode],
            'arg': argument,
            'value': value,
            'line': line,
        })
    return result


def _literal_subscript_lines(code, key):
    instructions = _instructions(code)
    lines = []
    for index, instruction in enumerate(instructions):
        if instruction['opname'] != 'BINARY_SUBSCR' or index == 0:
            continue
        previous = instructions[index - 1]
        if previous['opname'] == 'LOAD_CONST' and previous['value'] == key:
            lines.append(instruction['line'])
    return lines


def _has_membership_guard(code, key):
    instructions = _instructions(code)
    for index, instruction in enumerate(instructions):
        if instruction['opname'] != 'COMPARE_OP' or instruction['arg'] != 6:
            continue
        start = max(0, index - 4)
        if any(item['opname'] == 'LOAD_CONST' and item['value'] == key
               for item in instructions[start:index]):
            return True
    return False


def _raw_server_settings_subscripts(code):
    """Return literal keys indexed from raw ``serverSettings`` data flow."""
    instructions = _instructions(code)
    aliases = set()
    for index, instruction in enumerate(instructions):
        if (instruction['opname'] == 'STORE_FAST' and index and
                instructions[index - 1]['opname'] == 'LOAD_ATTR' and
                instructions[index - 1]['value'] == 'serverSettings'):
            aliases.add(instruction['value'])

    result = []
    for index, instruction in enumerate(instructions):
        if instruction['opname'] != 'BINARY_SUBSCR' or index < 2:
            continue
        key_instruction = instructions[index - 1]
        base_instruction = instructions[index - 2]
        if (key_instruction['opname'] != 'LOAD_CONST' or
                not isinstance(key_instruction['value'], basestring)):
            continue
        direct = (base_instruction['opname'] == 'LOAD_ATTR' and
                  base_instruction['value'] == 'serverSettings')
        aliased = (base_instruction['opname'] == 'LOAD_FAST' and
                   base_instruction['value'] in aliases)
        if direct or aliased:
            result.append((key_instruction['value'], instruction['line']))
    return result


def _assignment_literal(path, assignment_name):
    with open(path, 'rb') as source_file:
        tree = ast.parse(source_file.read(), path)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == assignment_name:
                return ast.literal_eval(node.value)
    raise ValueError('%s does not assign %s' % (path, assignment_name))


def _load_producers(port_root):
    module_root = os.path.join(
        port_root, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
        'offline_lan_0922')
    compat_path = os.path.join(module_root, 'compat.py')
    data_path = os.path.join(module_root, 'account_rpc', 'data.py')
    settings = _assignment_literal(compat_path, '_SERVER_SETTINGS')
    namespace = {'__name__': '_offline_lan_0922_audit_data'}
    with open(data_path, 'rb') as source_file:
        source = source_file.read()
    exec compile(source, data_path, 'exec') in namespace
    return {
        'serverSettings': settings,
        'shop': namespace['shop'](),
        'syncData': namespace['sync_data'](),
    }


def _lookup(mapping, path):
    value = mapping
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def _check_producer_shape(requirement, producers):
    value = _lookup(producers, requirement['producerPath'])
    shape = requirement['shape']
    if shape == 'sequence[2]':
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ValueError(
                '%s must be a two-item sequence' %
                requirement['producerPath'])
    elif shape == 'sequence[4]':
        if not isinstance(value, (tuple, list)) or len(value) != 4:
            raise ValueError(
                '%s must be a four-item sequence' %
                requirement['producerPath'])
    elif shape == 'int':
        # bool is intentionally rejected; the stock referral controller uses
        # this as a numeric position, not as an enabled flag.
        if not isinstance(value, (int, long)) or isinstance(value, bool):
            raise ValueError(
                '%s must be an int' % requirement['producerPath'])
    elif shape == 'mapping':
        if not isinstance(value, dict):
            raise ValueError(
                '%s must be a mapping' % requirement['producerPath'])
    elif shape == 'bool':
        if not isinstance(value, bool):
            raise ValueError(
                '%s must be a bool' % requirement['producerPath'])
    elif shape == 'non-empty-sequence':
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError(
                '%s must be a non-empty sequence' %
                requirement['producerPath'])
    elif shape == 'sequence[2][2]':
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ValueError(
                '%s must contain two periods' % requirement['producerPath'])
        for period in value:
            if not isinstance(period, (tuple, list)) or len(period) != 2:
                raise ValueError(
                    '%s periods must contain two values' %
                    requirement['producerPath'])
    else:
        raise ValueError('unknown producer shape: %s' % shape)


def _check_controller_shapes(producers):
    play_limits = _lookup(producers, 'syncData.stats.playLimits')
    if not isinstance(play_limits, (tuple, list)) or len(play_limits) != 2:
        raise ValueError('syncData.stats.playLimits must contain two periods')
    for period in play_limits:
        if not isinstance(period, (tuple, list)) or len(period) != 2:
            raise ValueError(
                'each syncData.stats.playLimits period must contain two values')

    mappings = (
        'syncData.stats.restrictions',
        'syncData.stats.vehTypeLocks',
        'syncData.stats.globalVehicleLocks',
        'syncData.stats.refSystem',
    )
    for path in mappings:
        if not isinstance(_lookup(producers, path), dict):
            raise ValueError('%s must be a mapping' % path)

    daily_hours = _lookup(producers, 'syncData.stats.dailyPlayHours')
    if not isinstance(daily_hours, (tuple, list)) or not daily_hours:
        raise ValueError(
            'syncData.stats.dailyPlayHours must be a non-empty sequence')

    for path in ('syncData.stats.credits', 'syncData.stats.gold',
                 'syncData.stats.crystal'):
        value = _lookup(producers, path)
        if (not isinstance(value, (int, long, float)) or
                isinstance(value, bool)):
            raise ValueError('%s must be numeric' % path)

    wallet_cache = _lookup(
        producers, 'syncData.cache.mayConsumeWalletResources')
    if not isinstance(wallet_cache, bool):
        raise ValueError(
            'syncData.cache.mayConsumeWalletResources must be a bool')


def audit(client_root, port_root):
    package_path = os.path.join(
        os.path.abspath(client_root), 'res', 'packages', 'scripts.pkg')
    if not os.path.isfile(package_path):
        raise ValueError('scripts.pkg not found: %s' % package_path)

    producer_values = _load_producers(os.path.abspath(port_root))
    hard_evidence = []
    guarded_evidence = []
    fallback_evidence = []
    safe_stats_evidence = []
    raw_settings_evidence = []
    lobby_callbacks = []

    with zipfile.ZipFile(package_path, 'r') as archive:
        names = set(archive.namelist())

        discovered_raw_settings = {}
        for member in sorted(name for name in names if name.endswith('.pyc')):
            root = _read_code(archive, member)
            for function_name, code in _walk_code(root):
                for key, line in _raw_server_settings_subscripts(code):
                    identity = (member, function_name, key)
                    discovered_raw_settings.setdefault(identity, []).append(line)
        expected_raw_settings = set(RAW_SERVER_SETTINGS_ACCESS)
        actual_raw_settings = set(discovered_raw_settings)
        if actual_raw_settings != expected_raw_settings:
            unclassified = sorted(actual_raw_settings - expected_raw_settings)
            missing = sorted(expected_raw_settings - actual_raw_settings)
            raise ValueError(
                'raw serverSettings consumer inventory changed; '
                'unclassified=%r missing=%r' % (unclassified, missing))
        for identity in sorted(actual_raw_settings):
            member, function_name, key = identity
            classification = RAW_SERVER_SETTINGS_ACCESS[identity]
            code = dict(_walk_code(_read_code(archive, member)))[function_name]
            if (classification == 'membership-guarded' and
                    not _has_membership_guard(code, key)):
                raise ValueError(
                    '%s:%s lost membership guard for %r' %
                    (member, function_name, key))
            if (classification == 'exception-fallback' and
                    'SETUP_EXCEPT' not in [item['opname']
                                           for item in _instructions(code)]):
                raise ValueError(
                    '%s:%s lost exception fallback for %r' %
                    (member, function_name, key))
            raw_settings_evidence.append({
                'member': member,
                'function': function_name,
                'key': key,
                'classification': classification,
                'lines': sorted(discovered_raw_settings[identity]),
            })

        for requirement in HARD_SUBSCRIPTS:
            root = _read_code(archive, requirement['member'])
            functions = dict(_walk_code(root))
            code = functions.get(requirement['function'])
            if code is None:
                raise ValueError(
                    '%s: missing %s' %
                    (requirement['member'], requirement['function']))
            lines = _literal_subscript_lines(code, requirement['key'])
            if not lines:
                raise ValueError(
                    '%s:%s no longer directly indexes %r' %
                    (requirement['member'], requirement['function'],
                     requirement['key']))
            for followup_index in requirement.get('followupIndices', ()):
                if not _literal_subscript_lines(code, followup_index):
                    raise ValueError(
                        '%s:%s no longer indexes follow-up position %d' %
                        (requirement['member'], requirement['function'],
                         followup_index))
            _check_producer_shape(requirement, producer_values)
            hard_evidence.append(dict(requirement, lines=lines))

        settings_member = 'scripts/client/helpers/server_settings.pyc'
        settings_root = _read_code(archive, settings_member)
        settings_functions = dict(_walk_code(settings_root))
        settings_init = settings_functions.get('ServerSettings.__init__')
        if settings_init is None:
            raise ValueError('%s: missing ServerSettings.__init__' %
                             settings_member)
        for key in GUARDED_SERVER_SETTINGS:
            lines = _literal_subscript_lines(settings_init, key)
            # clanProfile is passed through to a helper which performs the
            # direct subscript after this constructor's membership test.
            if key == 'clanProfile':
                helper = settings_functions.get('ServerSettings.__updateClanProfile')
                lines = _literal_subscript_lines(helper, key) if helper else []
            if not lines or not _has_membership_guard(settings_init, key):
                raise ValueError(
                    '%s: guarded direct access changed for %r' %
                    (settings_member, key))
            guarded_evidence.append({
                'path': 'serverSettings.' + key,
                'lines': lines,
                'required': False,
            })

        requester_member = (
            'scripts/client/gui/shared/utils/requesters/ShopRequester.pyc')
        requester_root = _read_code(archive, requester_member)
        requester_functions = dict(_walk_code(requester_root))
        getter_names = {
            'itemPrices': 'ShopCommonStats.getPrices',
            'notInShopItems': 'ShopCommonStats.getHiddens',
            'vehiclesNotToBuy': 'ShopCommonStats.getNotToBuyVehicles',
            'vehiclesRentPrices': 'ShopCommonStats.getVehicleRentPrices',
            'vehiclesToSellForGold': 'ShopCommonStats.getVehiclesForGold',
            'vehicleSellPriceFactors': (
                'ShopCommonStats.getVehiclesSellPriceFactors'),
            'prices': 'ShopCommonStats.getBoosterPrices',
            'notInShop': 'ShopCommonStats.getHiddenBoosters',
        }
        for parent, keys in sorted(FALLBACK_SHOP_KEYS.items()):
            for key in keys:
                function_name = getter_names[key]
                code = requester_functions.get(function_name)
                if code is None or not _literal_subscript_lines(code, key):
                    raise ValueError(
                        '%s: missing fallback consumer %s[%r]' %
                        (requester_member, function_name, key))
                if 'KeyError' not in code.co_names or \
                        'SETUP_EXCEPT' not in [item['opname']
                                               for item in _instructions(code)]:
                    raise ValueError(
                        '%s: %s no longer protects %r with KeyError fallback' %
                        (requester_member, function_name, key))
                value = _lookup(producer_values, 'shop.' + parent)
                if key not in value:
                    raise ValueError(
                        'shop.%s is missing deterministic fallback key %s' %
                        (parent, key))
                fallback_evidence.append({
                    'path': 'shop.%s.%s' % (parent, key),
                    'function': function_name,
                    'required': False,
                    'producerSupplied': True,
                })

        stats_member = (
            'scripts/client/gui/shared/utils/requesters/StatsRequester.pyc')
        stats_root = _read_code(archive, stats_member)
        stats_functions = dict(_walk_code(stats_root))
        for key, function_name in sorted(SAFE_STATS_GETTERS.items()):
            code = stats_functions.get(function_name)
            if (code is None or 'getCacheValue' not in code.co_names or
                    key not in code.co_consts):
                raise ValueError(
                    '%s: safe getter changed for %s' %
                    (stats_member, key))
            safe_stats_evidence.append({
                'key': key,
                'function': function_name,
                'hardSubscript': False,
            })

        _check_controller_shapes(producer_values)

        controller_members = [
            name for name in names
            if (name.startswith('scripts/client/gui/game_control/') and
                name.endswith('.pyc'))
        ]
        new_year_member = 'scripts/client/new_year/new_year_controller.pyc'
        if new_year_member in names:
            controller_members.append(new_year_member)
        for member in sorted(controller_members):
            root = _read_code(archive, member)
            for function_name, code in _walk_code(root):
                if code.co_name == 'onLobbyStarted':
                    lobby_callbacks.append('%s:%s' % (member, function_name))

    return {
        'clientRoot': os.path.abspath(client_root),
        'pythonRuntime': '%d.%d.%d' % sys.version_info[:3],
        'onLobbyStartedCallbacksScanned': sorted(lobby_callbacks),
        'hardDirectSubscripts': hard_evidence,
        'guardedServerSettingsSubscripts': guarded_evidence,
        'fallbackShopSubscripts': fallback_evidence,
        'safeStatsRequesterGetters': safe_stats_evidence,
        'rawServerSettingsConsumerInventory': raw_settings_evidence,
        'controllerShapes': {
            'syncData.stats.playLimits': [2, 2],
            'syncData.stats.dailyPlayHours': 'non-empty sequence',
            'syncData.stats.restrictions': 'mapping',
            'syncData.stats.vehTypeLocks': 'mapping',
            'syncData.stats.globalVehicleLocks': 'mapping',
            'syncData.stats.refSystem': 'mapping',
            'syncData.stats.money': 'numeric credits/gold/crystal',
            'syncData.cache.mayConsumeWalletResources': 'bool',
        },
        'conditionalRankedKeysWhenEnabled': [
            'serverSettings.ranked_config.cycleTimes',
            'serverSettings.ranked_config.seasons.*.cycles',
        ],
        'producerMissingHardRequirements': [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Audit #1513 lobby consumers and offline producers.')
    parser.add_argument('client_root')
    parser.add_argument(
        '--port-root',
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (2, 7):
        parser.error('this auditor must run under CPython 2.7')
    try:
        report = audit(args.client_root, args.port_root)
    except (IOError, KeyError, TypeError, ValueError, zipfile.BadZipfile) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
