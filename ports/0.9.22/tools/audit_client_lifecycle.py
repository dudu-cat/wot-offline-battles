#!/usr/bin/env python2
"""Verify exact #1513 lifecycle ordering that signatures cannot express."""

from __future__ import print_function

import argparse
import json
import os
import sys
import zipfile

from audit_client_abi import _read_module_contract
from audit_lobby_consumers import _instructions


ORDERED_USES = (
    (
        'scripts/client/ChatManager.pyc',
        'ChatManager.switchPlayerProxy',
        ('_ChatManager__cleanupMyCallbacks', 'proxy', 'playerProxy'),
        'old chat proxy cleanup precedes replacement proxy assignment',
    ),
    (
        'scripts/client_common/ClientChat.pyc',
        'ClientChat.__init__',
        ('self', '_ClientChat__chatActionCallbacks'),
        'every chat-capable player initializes its callback registry',
    ),
    (
        'scripts/client/Account.pyc',
        'PlayerAccount.onBecomeNonPlayer',
        ('chatManager', 'switchPlayerProxy', 'syncData',
         'onAccountBecomeNonPlayer', 'events',
         'onAccountBecomeNonPlayer'),
        'Account detaches chat and helpers before GUI retirement completes',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.onBecomeNonPlayer',
        ('chatManager', 'switchPlayerProxy', 'g_playerEvents',
         'onAvatarBecomeNonPlayer'),
        'Avatar detaches chat before publishing its retirement event',
    ),
    (
        'scripts/client/account_helpers/AccountSyncData.pyc',
        'AccountSyncData.setAccount',
        ('_AccountSyncData__account',
         '_AccountSyncData__savePersistentCache',
         '_AccountSyncData__persistentCache', 'setAccount'),
        'persistent cache save precedes replacement weak-proxy binding',
    ),
    (
        'scripts/client/account_helpers/persistent_caches.pyc',
        'SimpleCache.setAccount',
        ('weakref', 'proxy', '_SimpleCache__account'),
        'Account cache stores a weak proxy',
    ),
    (
        'scripts/client/account_helpers/persistent_caches.pyc',
        'cacheFileName',
        ('name', '__class__', '__name__'),
        'cache filename dereferences Account identity fields',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'LoginState.init',
        ('_clearEntitiesAndSpaces', '_updateDscDesc'),
        'LoginState clears client-only entities before normal initialization',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'LoginState.update',
        ('_clearEntitiesAndSpaces', '_updateDscDesc'),
        'LoginState update repeats the destructive entity boundary',
    ),
    (
        'scripts/client/gui/shared/personality.pyc',
        'onAccountShowGUI',
        ('g_hangarSpace', 'g_currentVehicle', 'showLobby'),
        'native Account GUI owns asynchronous hangar then lobby transition',
    ),
    (
        'scripts/client/gui/shared/personality.pyc',
        'onAccountBecomeNonPlayer',
        ('g_currentVehicle', 'destroy',
         'g_currentPreviewVehicle', 'destroy',
         'g_hangarSpace', 'destroy'),
        'Account retirement destroys lobby vehicles before hangar space',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.onBecomePlayer',
        ('cameraSpaceID', 'g_hangarSpace', 'destroy',
         'ClientArena', 'arenaType', 'abort'),
        'Avatar promotion retires hangar before validating its arena',
    ),
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.create',
        ('showBattlePage', 'createSpace', 'createEntity', 'player', 'cancel'),
        'stock map creation can cancel after partial player construction',
    ),
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.destroy',
        ('clearEntitiesAndSpaces', 'cancel'),
        'stock map destroy falls back to lossy cancel on failure',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.leaveArena',
        ('base', 'leaveArena', 'BattleReplay'),
        'server leave mailbox returns before native Avatar cleanup completes',
    ),
)


REQUIRED_USES = (
    (
        'scripts/client/gui/app_loader/states.pyc',
        'LoginState._clearEntitiesAndSpaces',
        ('BigWorld', 'clearEntitiesAndSpaces'),
        'LoginState uses the engine entity-and-space clear',
    ),
    (
        'scripts/client/Account.pyc',
        'PlayerAccount.onBecomePlayer',
        ('BigWorld', 'clearAllSpaces'),
        'Account promotion normally clears all spaces',
    ),
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.cancel',
        ('worldDrawEnabled', 'setWatcher'),
        'map cancel only resets presentation state',
    ),
)


FORBIDDEN_USES = (
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.cancel',
        ('clearEntitiesAndSpaces', 'clearAllSpaces', 'clearSpace',
         'releaseSpace'),
        'map cancel must not be mistaken for ownership cleanup',
    ),
)


EXPECTED_ACCOUNT_BINDERS = (
    ('scripts/client/account_helpers/AccountSyncData.pyc',
     'AccountSyncData.setAccount'),
    ('scripts/client/account_helpers/ClientBadges.pyc',
     'ClientBadges.setAccount'),
    ('scripts/client/account_helpers/ClientGoodies.pyc',
     'ClientGoodies.setAccount'),
    ('scripts/client/account_helpers/ClientNewYear.pyc',
     'ClientNewYear.setAccount'),
    ('scripts/client/account_helpers/ClientRanked.pyc',
     'ClientRanked.setAccount'),
    ('scripts/client/account_helpers/DossierCache.pyc',
     'DossierCache.setAccount'),
    ('scripts/client/account_helpers/Inventory.pyc',
     'Inventory.setAccount'),
    ('scripts/client/account_helpers/QuestProgress.pyc',
     'QuestProgress.setAccount'),
    ('scripts/client/account_helpers/Shop.pyc', 'Shop.setAccount'),
    ('scripts/client/account_helpers/Stats.pyc', 'Stats.setAccount'),
    ('scripts/client/account_helpers/client_recycle_bin.pyc',
     'ClientRecycleBin.setAccount'),
    ('scripts/client/account_helpers/persistent_caches.pyc',
     'SimpleCache.setAccount'),
    ('scripts/client/account_helpers/vehicle_rotation.pyc',
     'VehicleRotation.setAccount'),
)


def _code(archive, cache, member, function):
    if member not in cache:
        unused_signatures, code_objects, unused_globals = \
            _read_module_contract(archive, member)
        cache[member] = code_objects
    value = cache[member].get(function)
    if value is None:
        raise ValueError('%s: missing %s' % (member, function))
    return value


def _ordered_offsets(code, names):
    instructions = _instructions(code)
    offsets = []
    after = -1
    for name in names:
        match = None
        for instruction in instructions:
            if (instruction['offset'] > after and
                    instruction['value'] == name):
                match = instruction['offset']
                break
        if match is None:
            return None
        offsets.append(match)
        after = match
    return offsets


def audit(client_root):
    package_path = os.path.join(
        os.path.abspath(client_root), 'res', 'packages', 'scripts.pkg')
    if not os.path.isfile(package_path):
        raise ValueError('scripts.pkg not found: %s' % package_path)
    errors = []
    checked = []
    cache = {}
    with zipfile.ZipFile(package_path, 'r') as archive:
        names = set(archive.namelist())
        members = set(item[0] for item in (
            ORDERED_USES + REQUIRED_USES + FORBIDDEN_USES))
        missing = sorted(member for member in members if member not in names)
        errors.extend('missing bytecode member: %s' % member
                      for member in missing)
        actual_binders = []
        for member in sorted(names):
            if (not member.startswith('scripts/client/account_helpers/') or
                    not member.endswith('.pyc')):
                continue
            try:
                signatures, unused_codes, unused_globals = \
                    _read_module_contract(archive, member)
            except (KeyError, ValueError):
                continue
            for function in signatures:
                if function.endswith('.setAccount'):
                    actual_binders.append((member, function))
        actual_binders = tuple(sorted(actual_binders))
        expected_binders = tuple(sorted(EXPECTED_ACCOUNT_BINDERS))
        if actual_binders != expected_binders:
            errors.append(
                'Account setAccount inventory changed: actual=%r expected=%r' %
                (actual_binders, expected_binders))
        else:
            checked.append({
                'contract': 'complete Account helper binding inventory',
                'binders': len(actual_binders),
            })
        for member, function, expected, reason in ORDERED_USES:
            if member in missing:
                continue
            code = _code(archive, cache, member, function)
            offsets = _ordered_offsets(code, expected)
            if offsets is None:
                errors.append('%s:%s violates order %r' %
                              (member, function, expected))
            else:
                checked.append({
                    'member': member, 'function': function,
                    'contract': reason, 'offsets': offsets,
                })
        for member, function, expected, reason in REQUIRED_USES:
            if member in missing:
                continue
            code = _code(archive, cache, member, function)
            instructions = _instructions(code)
            used = set(item['value'] for item in instructions)
            absent = tuple(name for name in expected if name not in used)
            if absent:
                errors.append('%s:%s missing lifecycle names %r' %
                              (member, function, absent))
            else:
                checked.append({
                    'member': member, 'function': function,
                    'contract': reason,
                })
        for member, function, forbidden, reason in FORBIDDEN_USES:
            if member in missing:
                continue
            code = _code(archive, cache, member, function)
            instructions = _instructions(code)
            used = set(item['value'] for item in instructions)
            present = tuple(name for name in forbidden if name in used)
            if present:
                errors.append('%s:%s unexpectedly uses %r' %
                              (member, function, present))
            else:
                checked.append({
                    'member': member, 'function': function,
                    'contract': reason,
                })
    if errors:
        raise ValueError('; '.join(errors))
    return {
        'clientRoot': os.path.abspath(client_root),
        'pythonRuntime': '%d.%d.%d' % sys.version_info[:3],
        'checkedLifecycleContracts': len(checked),
        'contracts': checked,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Audit exact WoT #1513 lifecycle ordering read-only.')
    parser.add_argument('client_root')
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (2, 7):
        parser.error('this auditor must run under CPython 2.7')
    try:
        report = audit(args.client_root)
    except (IOError, KeyError, ValueError, zipfile.BadZipfile) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
