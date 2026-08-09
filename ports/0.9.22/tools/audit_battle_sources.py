#!/usr/bin/env python
"""Fail when a #1513 module has no documented 0.8.2/source provenance."""

from __future__ import print_function

import hashlib
import os
import re
import sys


PORT_FILES = {
    '__init__.py': 'port_metadata',
    'account_rpc/__init__.py': '1513_account_api',
    'account_rpc/commands.py': '1513_account_api',
    'account_rpc/data.py': '1513_account_api',
    'account_rpc/requests.py': '1513_account_api',
    'account_rpc/server.py': '1513_account_api',
    'account_rpc/state.py': '1513_account_api',
    'ai/__init__.py': 'package_adapter',
    'ai/adapter.py': 'server_authority_adapter',
    'ai/cover.py': 'exact_082',
    'ai/driver.py': '082_latest_intent_steering_plus_1513_descriptor_adapter',
    'ai/maps.py': '082_import_adapter_plus_0922_data',
    'ai/maps_0922_extra.py': '0922_map_data',
    'ai/maps_extra.py': '082_whitespace_only',
    'ai/maps_group_a.py': '082_routes_plus_0922_resource_data',
    'ai/maps_group_b.py': '082_routes_plus_0922_resource_data',
    'ai/maps_group_c.py': '082_whitespace_only',
    'ai/navigation.py': '082_navigation_performance_plus_shallow_water_guard',
    'ai/planner.py': '082_latest_law_plus_spawn_join',
    'battle_feedback.py': '082_law_plus_1513_presenter',
    'battle_runtime.py': '1513_native_entity_adapter',
    'bootstrap.py': '1513_lifecycle_adapter',
    'bot_runtime.py': 'server_authority_plus_082_repair_fire_and_handoff_adapter',
    'combat_rules.py': '082_law_adapter',
    'critical_damage.py': '082_generated_closure_adapter',
    'compat.py': '1513_lifecycle_adapter',
    'config.py': '082_config_adapter',
    'destructibles_authority.py': 'exact_082',
    'destructibles_compat.py': '1513_destructibles_api_adapter',
    'destructibles_sensor.py': '082_closure_adapter',
    'device_damage.py': '082_law_plus_1513_descriptor_adapter',
    'entities/__init__.py': 'package_adapter',
    'entities/avatar_server.py': '1513_avatar_api',
    'entities/bigworld_binding.py': '1513_entity_api',
    'entities/remote_vehicle.py': '082_carrier_plus_1513_model_adapter',
    'entities/runtime.py': '1513_entity_api',
    'foliage.py': '082_foliage_law_port',
    'gun_mechanics.py': '082_inline_law_adapter',
    'lan_client.py': '082_protocol_adapter',
    'lan_session.py': '082_queue_adapter',
    'internal_geometry.py': 'exact_082',
    'internal_hit_layouts.py': '082_import_adapter',
    'internal_layout_profiles.py': 'exact_082',
    'internal_layout_store.py': '082_import_adapter',
    'map_catalog.py': '1513_arena_api',
    'navigation_graph_schema.py': '1513_navigation_release_contract',
    'prebaked_foliage.py': '082_foliage_data_adapter',
    'prebaked_navigation.py': '082_navigation_graph_adapter',
    'queue_ui.py': '1513_scaleform_adapter',
    'snapshot_sync.py': '082_protocol_adapter',
    'spawn_planner.py': '082_law_plus_1513_arena_data',
    'spotting.py': '082_law_plus_1513_descriptor_data',
    'tank_collision.py': '082_current_collision_and_spatial_index_port',
    'user_config.py': '1513_path_adapter',
    'vehicle_physics.py': '082_latest_calibrated_law_port',
    'world_collision.py': '082_closure_adapter',
}

# The #1513 battle also depends on two Python 3 service modules outside the
# wotmod.  They are part of the same provenance gate because they own shared
# room/rule/macro state, not deployment-only infrastructure.
SERVER_FILES = {
    'lan_battle_server.py': '082_protocol_and_shared_law_adapter',
    'server_bot_ai.py': '082_current_server_macro_planner',
}

# The working 0.8.2 implementation was finalized in a separate release
# worktree.  A public release cannot depend on that local checkout, so adapted
# modules are pinned here after source review. Most remain based on commit
# 7e3a1b2; the five performance/control files below include the reviewed
# 2026-08-08 release-worktree delta measured on the legacy client. This makes
# subsequent drift fail deterministically instead of silently comparing
# against the older source left in this repository's main checkout.
FINAL_082_BASELINE = '7e3a1b2969e839b8e00acab0a0e3bc39f8bcc48c'
PINNED_PORT_SHA256 = {
    'ai/adapter.py': '7abfd2af9a9e1f39a5fe7143ef729af4431430b06f3a6d0bac4e669449ea00f4',
    'ai/driver.py': '692b2385f9cb701b60012b3b5ff2b565770a7c1e97b58038bc8904f9c3448356',
    'ai/maps.py': 'c55424c653e92f238284de4851583d346e2666fffa4a9edbd3a59a693595bdfe',
    'ai/maps_group_a.py': '35a7d8b34c1c78f889bbe30d54d7898fd581259769d1aa13112eda3f32afcc7f',
    'ai/maps_group_b.py': '152aae3d25921998b9436c3fbe2210d6f640c92220cf1729504fd59ce7b952d1',
    'ai/navigation.py': '2a0bac86694100d94ae3e383e9ce2cc4cc1fa3f1c05b52d78fa3b9dc009565b9',
    'ai/planner.py': '5bea9f55a6b23e3f4f24ad5a337eb2e7e92acd72a6482496ab98a40eff90f453',
    'bot_runtime.py': '8bc74bdba5f29cd719ed88b7c934a59ffc928acae3f8bc4fd061c166e7858b8b',
    'entities/remote_vehicle.py': 'f35b0b7a45179ed5032fcb9503e032f1b4cc7774be8dd5d0709a3add2550ad2f',
    'tank_collision.py': '2f0b9b8987b4401d430e84512e5fdbf84351b4245e2589907d597a8508197b2d',
    'vehicle_physics.py': '9beab9b11f8c349d55979c7a753f33b48262b1add28f63f3d754a8495c20e676',
    'world_collision.py': '6def0448c6c79b324b822ae79b397775815c5e4d66709963a5ff52fb38608de1',
}
PINNED_SERVER_SHA256 = {
    'server_bot_ai.py': '95248c44d57564a3220305342241233237c103865ea9ce684b5cefe5f0c918e9',
}

# Every Python file in the working 0.8.2 offhangar tree is classified here.
# This reverse inventory prevents a useful source module from disappearing
# merely because the #1513 package never mentioned it.
SOURCE_FILE_CLASSES = {}


def _source_group(classification, names):
    for name in names.split():
        SOURCE_FILE_CLASSES[name] = classification


_source_group('copied_law', '''
bot_ai.py bot_ai_cover.py bot_ai_driver.py bot_ai_maps.py
bot_ai_maps_extra.py bot_ai_maps_group_a.py bot_ai_maps_group_b.py
bot_ai_maps_group_c.py bot_ai_navigation.py physics.py
''')
_source_group('law_port_or_open_gap', '''
destructibles_authority.py network_battle.py
offline_battle.py offline_battle_stack.py
''')
_source_group('1513_lobby_or_engine_adapter', '''
EXrequests.py _constants.py command_handlers.py command_router.py data.py
lan_settings.py lan_waiting_room.py paths.py pen_indicator.py server.py
session_guards.py state.py user_config.py
''')
_source_group('support_library', '''
__init__.py logging.py utils.py
''')
_source_group('copied_gameplay_support', '''
device_damage.py internal_geometry.py internal_hit_layouts.py internal_layout_profiles.py
internal_layout_store.py
''')
_source_group('optional_internal_debug', '''
internal_layout_debug.py physics_monitor.py
''')
_source_group('retired_development_tool', '''
bw_script.py dis_cam_update.py dis_cameras.py dis_rotate.py dis_setup.py
fix_app.py fix_app_regex.py fix_camera_bypass.py fix_camera_hook.py
fix_chassis_cleanly.py fix_chassis_crash.py fix_force_cam.py
fix_force_cam2.py fix_force_camera.py fix_force_camera2.py
fix_force_camera3.py fix_hook2.py fix_swinging_override.py
fix_target_yaw.py fix_typo.py inject_active_cam.py inject_active_cam2.py
inject_aih.py inject_enable_log.py inject_logger.py inject_logger_setup.py
inject_mouse.py inject_shift.py inject_swinging.py patch_manual_cam.py
remove_shift.py test_matrix.py
''')

EXACT_COPIES = {
    'ai/cover.py': 'bot_ai_cover.py',
    'destructibles_authority.py': 'destructibles_authority.py',
    'internal_geometry.py': 'internal_geometry.py',
    'internal_layout_profiles.py': 'internal_layout_profiles.py',
}

WHITESPACE_COPIES = {
    'ai/maps_extra.py': 'bot_ai_maps_extra.py',
    'ai/maps_group_c.py': 'bot_ai_maps_group_c.py',
}


def _read(path):
    with open(path, 'rb') as stream:
        return stream.read()


def _sha256(path):
    return hashlib.sha256(_read(path)).hexdigest()


def _text(path):
    value = _read(path)
    if not isinstance(value, str):
        value = value.decode('utf-8')
    return value


def _without_blank_lines(value):
    return '\n'.join(line.rstrip() for line in value.splitlines()
                     if line.strip())


def _normalise_newlines(value):
    return value.replace('\r\n', '\n').replace('\r', '\n')


def _normalise_planner(value):
    return value.replace(
        'from gui.mods.offline_lan_0922.ai import maps as bot_ai_maps',
        'from gui.mods.offhangar import bot_ai_maps')


def _normalise_maps(value):
    replacements = (
        ('from gui.mods.offline_lan_0922.ai import maps_group_a as '
         'bot_ai_maps_group_a',
         'from gui.mods.offhangar import bot_ai_maps_group_a'),
        ('from gui.mods.offline_lan_0922.ai import maps_group_b as '
         'bot_ai_maps_group_b',
         'from gui.mods.offhangar import bot_ai_maps_group_b'),
        ('from gui.mods.offline_lan_0922.ai import maps_group_c as '
         'bot_ai_maps_group_c',
         'from gui.mods.offhangar import bot_ai_maps_group_c'),
        ('from gui.mods.offline_lan_0922.ai import maps_extra as '
         'bot_ai_maps_extra',
         'from gui.mods.offhangar import bot_ai_maps_extra'),
    )
    for source, target in replacements:
        value = value.replace(source, target)
    value = value.replace(
        'from gui.mods.offline_lan_0922.ai import maps_0922_extra as '
        'bot_ai_maps_0922_extra\n', '')
    value = value.replace(
        'TACTICAL_MAPS.update(bot_ai_maps_0922_extra.TACTICAL_MAPS_0922_EXTRA)\n',
        '')
    return value


def _normalise_internal_imports(value):
    return value.replace(
        'gui.mods.offline_lan_0922', 'gui.mods.offhangar')


def _normalise_1513_descriptor_access(value):
    """Undo only the reviewed #1513 attribute adapters before comparison."""
    replacements = (
        ("_comp is not None", "_comp is not None and hasattr(_comp, 'get')"),
        ("_descriptor_value(_comp, 'itemTypeName', '')",
         "_comp.get('itemTypeName', '')"),
        ("_eng is not None", "_eng is not None and hasattr(_eng, 'get')"),
        ("_descriptor_value(_eng, 'fireStartingChance', 0.15)",
         "_eng.get('fireStartingChance', 0.15)"),
        ("_eng2 is not None", "_eng2 is not None and hasattr(_eng2, 'get')"),
        ("_descriptor_value(_eng2, 'fireStartingChance', 0.15)",
         "_eng2.get('fireStartingChance', 0.15)"),
    )
    for adapted, original in replacements:
        value = value.replace(adapted, original)
    return value


def _normalise_critical_proposal_guards(value):
    """Undo the reviewed detached-proposal presentation guards."""
    replacements = (
        ("if (not is_player_target or getattr(target_mock, "
         "'_offline_proposal_only', False)):\n\t\treturn",
         "if not is_player_target:\n\t\treturn"),
        ("if (is_player_target and not getattr(target_mock, "
         "'_offline_proposal_only', False)):\n\t\ttry:",
         "if is_player_target:\n\t\ttry:"),
        ("if (is_player_target and not getattr(mock, "
         "'_offline_proposal_only', False)):\n\t\ttry:",
         "if is_player_target:\n\t\ttry:"),
        ("if not getattr(target_mock, '_offline_proposal_only', False):\n"
         "\t\t\t\t\t\tBigWorld.player().arena.onVehicleKilled("
         "target_mock.id, attacker_id, 1)",
         "BigWorld.player().arena.onVehicleKilled(target_mock.id, "
         "attacker_id, 1)"),
        ("if (is_player_target and not getattr(target_mock, "
         "'_offline_proposal_only', False) and not "
         "getattr(target_mock, '_is_killed', False))",
         "if is_player_target and not getattr(target_mock, "
         "'_is_killed', False)"),
    )
    for adapted, original in replacements:
        value = value.replace(adapted, original)
    return value


def _top_level_function(value, name):
    """Return one function without surrounding top-level commentary."""
    lines = _normalise_newlines(value).split('\n')
    marker = 'def %s(' % name
    for index, line in enumerate(lines):
        if line.startswith(marker):
            block = [line]
            for following in lines[index + 1:]:
                if following and following[0] not in (' ', '\t'):
                    break
                block.append(following)
            return '\n'.join(block).rstrip()
    return None


def _block_at_any_indent(value, kind, name):
    """Return a class/function dedented from module or closure scope."""
    lines = _normalise_newlines(value).split('\n')
    marker = '%s %s' % (kind, name)
    for index, line in enumerate(lines):
        if not line.lstrip().startswith(marker):
            continue
        prefix = line[:-len(line.lstrip())]
        block = [line[len(prefix):]]
        for following in lines[index + 1:]:
            if not following.strip():
                block.append('')
                continue
            following_prefix = following[:-len(following.lstrip())]
            if len(following_prefix) <= len(prefix):
                break
            if following.startswith(prefix):
                following = following[len(prefix):]
            block.append(following)
        return '\n'.join(
            item.rstrip() for item in block if item.strip())
    return None


def _function_at_any_indent(value, name):
    return _block_at_any_indent(value, 'def', name)


def audit(repo_root):
    repo_root = os.path.abspath(repo_root)
    package = os.path.join(
        repo_root, 'ports', '0.9.22', 'src', 'res', 'scripts', 'client',
        'gui', 'mods', 'offline_lan_0922')
    original = os.path.join(
        repo_root, 'scripts', 'client', 'gui', 'mods', 'offhangar')
    actual = set()
    for root, unused_dirs, files in os.walk(package):
        for name in files:
            if name.endswith('.py'):
                actual.add(os.path.relpath(os.path.join(root, name), package))
    documented = set(PORT_FILES)
    errors = []
    for missing in sorted(actual - documented):
        errors.append('undocumented port module: %s' % missing)
    for stale in sorted(documented - actual):
        errors.append('documented module is missing: %s' % stale)
    source_actual = set(
        name for name in os.listdir(original) if name.endswith('.py'))
    source_documented = set(SOURCE_FILE_CLASSES)
    for missing in sorted(source_actual - source_documented):
        errors.append('unclassified 0.8.2 source module: %s' % missing)
    for stale in sorted(source_documented - source_actual):
        errors.append('classified 0.8.2 source module is missing: %s' % stale)
    audit_document = _text(os.path.join(
        repo_root, 'ports', '0.9.22', 'BATTLE_SOURCE_AUDIT.md'))
    # These are explicit non-parity boundaries, not optional prose.  Keeping
    # them in the executable gate prevents a green source inventory from being
    # mistaken for feature parity when the corresponding 0.8.2 path is absent.
    for required_gap in (
            'passive optional-device',
            'detailed battle-result statistics'):
        if required_gap not in audit_document:
            errors.append('source audit omits open parity gap: %s' %
                          required_gap)
    for port_name in sorted(actual):
        if '`%s`' % port_name not in audit_document:
            errors.append('port module lacks a written explanation: %s' %
                          port_name)
    for server_name in sorted(SERVER_FILES):
        if not os.path.isfile(os.path.join(repo_root, server_name)):
            errors.append('documented battle service is missing: %s' %
                          server_name)
        if '`%s`' % server_name not in audit_document:
            errors.append('battle service lacks a written explanation: %s' %
                          server_name)
    for source_name in sorted(source_actual):
        if '`%s`' % source_name not in audit_document:
            errors.append('0.8.2 module lacks a written disposition: %s' %
                          source_name)
    for port_name, original_name in sorted(EXACT_COPIES.items()):
        if _normalise_newlines(_text(os.path.join(package, port_name))) != \
                _normalise_newlines(_text(
                    os.path.join(original, original_name))):
            errors.append('%s diverged from 0.8.2 %s' %
                          (port_name, original_name))
    for port_name, original_name in sorted(WHITESPACE_COPIES.items()):
        if _without_blank_lines(_text(os.path.join(package, port_name))) != \
                _without_blank_lines(_text(
                    os.path.join(original, original_name))):
            errors.append('%s has non-whitespace divergence from 0.8.2 %s' %
                          (port_name, original_name))
    for port_name, expected in sorted(PINNED_PORT_SHA256.items()):
        if _sha256(os.path.join(package, port_name)) != expected:
            errors.append('%s diverged from reviewed final 0.8.2 port %s' %
                          (port_name, FINAL_082_BASELINE[:7]))
    for server_name, expected in sorted(PINNED_SERVER_SHA256.items()):
        if _sha256(os.path.join(repo_root, server_name)) != expected:
            errors.append('%s diverged from reviewed final 0.8.2 service %s' %
                          (server_name, FINAL_082_BASELINE[:7]))
    for port_name, original_name in (
            ('internal_hit_layouts.py', 'internal_hit_layouts.py'),
            ('internal_layout_store.py', 'internal_layout_store.py')):
        port_value = _normalise_internal_imports(
            _text(os.path.join(package, port_name)))
        if port_value != _text(os.path.join(original, original_name)):
            errors.append('%s has more than the documented package import change' %
                          port_name)
    battle_runtime = _text(os.path.join(package, 'battle_runtime.py'))
    for required in ('combat_rules.damage',
                     'combat_rules.resolve_hull_hit',
                     'score_candidates',
                     'SpawnPlanner', 'send_battle_ready',
                     'activeGunShotIndex', 'reported_health',
                     'arena_vehicle_statistics', 'arena_team_killer',
                     '_death_attacker_engine_id', '_activate_equipment',
                     'ACTIVATE_EQUIPMENT',
                     "critical_damage.stat_factor(entity, 'turret_speed')",
                     'clamp_vision_factor(',
                     "critical_damage.stat_factor(entity, 'vision')",
                     '_monotonic_time()', '_accepted_event_ids',
                     '_applied_event_ids', '_event_journal',
                     'def _prepare_ordered_event',
                     'def _drain_event_journal',
                     'def _pending_event_references'):
        if required not in battle_runtime:
            errors.append('battle_runtime.py does not reuse %s' % required)
    if "_field(shell, 'piercingPower'" in battle_runtime:
        errors.append('battle_runtime.py reads #1513 penetration from shell, not shot')
    if 'BOT_VEHICLE_CANDIDATES' in battle_runtime:
        errors.append('battle_runtime.py hard-codes a replacement bot lineup')
    for required in ('_spawn_cache', '_formation_pose',
                     'notifyInputKeysDown', 'RemoteVehicleFactory',
                     'native_motion=False', 'set_vehicle_pose',
                     'set_vehicle_pose_overlay', '_update_local_presentation',
                     'self._local_model.matrix = self._local_matrix',
                     '_update_spotting', 'spot_until',
                     'event.pop(\'pose\', None)'):
        if required not in battle_runtime:
            errors.append('battle_runtime.py omits #1513 movement/spawn '
                          'boundary: %s' % required)
    if 'entity.teleport(' in battle_runtime:
        errors.append('battle_runtime.py calls forbidden client-side '
                      'Entity.teleport')
    if 'authority_entity_resolver=self._server_entity' not in battle_runtime:
        errors.append('battle_runtime.py does not wire the private authority '
                      'entity resolver')
    server_entity = _function_at_any_indent(
        battle_runtime, '_server_entity') or ''
    private_lookup = server_entity.find('self._remote_factory.get(entity_id)')
    public_lookup = server_entity.find(
        'self._runtime.bigworld.entity(entity_id)')
    if (private_lookup < 0 or public_lookup < 0 or
            private_lookup > public_lookup):
        errors.append('battle_runtime.py authority lookup does not prefer '
                      'the private remote registry')
    on_events = _function_at_any_indent(battle_runtime, 'on_events') or ''
    for required in ('self._prepare_ordered_event(event)',
                     'self._accepted_event_ids.add(event_id)',
                     'self._drain_event_journal()', 'self._fail(error)'):
        if required not in on_events:
            errors.append('battle_runtime.py ordered event boundary omits %s' %
                          required)
    bigworld_binding = _text(os.path.join(
        package, 'entities', 'bigworld_binding.py'))
    for name in ('set_vehicle_pose', 'update_vehicle_aim'):
        function = _function_at_any_indent(bigworld_binding, name) or ''
        if '_authority_entity_or_fail(entity_id)' not in function:
            errors.append('bigworld_binding.py %s bypasses the private '
                          'authority lookup' % name)
    for name in ('start_vehicle_visual', 'avatar_vehicle_entered',
                 'avatar_client_ready', 'drive_vehicle'):
        function = _function_at_any_indent(bigworld_binding, name) or ''
        if '_entity_or_fail(' not in function:
            errors.append('bigworld_binding.py %s bypasses the public native '
                          'entity lookup' % name)
    ready_function = _function_at_any_indent(
        bigworld_binding, 'is_vehicle_ready') or ''
    if 'self._bigworld.entity(entity_id)' not in ready_function:
        errors.append('bigworld_binding.py readiness bypasses the public '
                      'native entity lookup')
    bot_runtime = _text(os.path.join(package, 'bot_runtime.py'))
    for required in ('vehicle_physics.longitudinal_step',
                     'vehicle_physics.traverse_step', 'TerrainNavigator',
                     'cover_probe', '_update_vertical_motion',
                     '_guard_realised_pose', 'physics_ground_probe',
                     "'vertical_speed'", "'airborne'", "'view_range'",
                     'tank_collision.resolve_tank', '_BotGunState',
                     '_dispersed_barrel_angles', '_update_gun_aim',
                     'critical_damage.tick_repair',
                     'critical_damage.tick_fire',
                     "'combat_base_revision'", "'combat_ack_seq'",
                     "'combat_fire_elapsed'", "'combat_fire_timer'",
                     "'authority_handoff_pending'",
                     'handoff_canonical_reset'):
        if required not in bot_runtime:
            errors.append('bot_runtime.py does not reuse %s' % required)
    for forbidden in ('maximum_turn = 0.85', "'affordances': []"):
        if forbidden in bot_runtime:
            errors.append('bot_runtime.py retains replacement law: %s' %
                          forbidden)
    if 'def apply_ground(' in bot_runtime:
        errors.append('bot_runtime.py retains the removed historical '
                      'ground-snap helper')
    if "result['world_pose'] = True" not in bot_runtime:
        errors.append('bot_runtime.py can resolve one spawn slot twice')
    bigworld_binding = _text(os.path.join(
        package, 'entities', 'bigworld_binding.py'))
    if 'vehicle_filter.notifyInputKeysDown(' not in bigworld_binding:
        errors.append('bigworld_binding.py omits the exact #1513 native '
                      'vehicle input boundary')
    if 'vehicle_filter.set(' in bigworld_binding:
        errors.append('bigworld_binding.py assumes WGVehicleFilter exposes '
                      'the absent generic set method')
    if 'vehicle_filter.setPosition(' in bigworld_binding:
        errors.append('bigworld_binding.py assumes the server-only '
                      'setPosition helper exists on a client-created filter')
    if 'entity.teleport(' in bigworld_binding:
        errors.append('bigworld_binding.py calls forbidden client-side '
                      'Entity.teleport')
    if '_ConsistentMatrices__setTarget' not in bigworld_binding:
        errors.append('bigworld_binding.py omits the exact #1513 attached '
                      'vehicle matrix binding used by the minimap')
    remote_vehicle = _text(os.path.join(
        package, 'entities', 'remote_vehicle.py'))
    for required in ('class RemoteVehicle', 'class RemoteVehicleFactory',
                     "'OfflineEntity'", 'prepareCompoundAssembler',
                     'loadResourceListBG', 'def set_pose',
                     'def collideSegmentExt', 'collide_vehicle_at_matrix',
                     'ProjectileMover', 'setupTurretRotations',
                     'assembleRecoil', 'extrasDict',
                     'self.model.matrix = self.matrix',
                     '_SegmentCollisionResult'):
        if required not in remote_vehicle:
            errors.append('remote_vehicle.py omits copied carrier boundary: '
                          '%s' % required)
    if 'PyModelObstacle' in remote_vehicle:
        errors.append('remote_vehicle.py installs a second live tank collider '
                      'beside the copied circle-chain resolver')
    for required in (
            'class _RemoteEngineAudition', 'def getSoundObject',
            'WWgetSoundObject', 'self.engineAudition',
            'self.onModelChanged', 'self.isObserver',
            'self.isPlayerVehicle', 'self.isAlive',
            'def collideSegment', 'def segmentMayHitEntity',
            'def changeVisibility', 'self.compoundModel.visible'):
        if required not in remote_vehicle:
            errors.append(
                'remote_vehicle.py omits a surveyed #1513 consumer API: %s' %
                required)
    shoot_effect = _block_at_any_indent(
        remote_vehicle, 'def', '_start_shooting_effect') or ''
    for required in ('extra.stopFor(self)', 'extra.startFor(self'):
        if required not in shoot_effect:
            errors.append(
                'remote shot effect omits exact #1513 call: %s' % required)
    if 'except Exception' in shoot_effect:
        errors.append(
            'remote shot effect hides a #1513 consumer contract failure')
    bigworld_binding = _text(os.path.join(
        package, 'entities', 'bigworld_binding.py'))
    for required in (
            'def start_vehicle_visual', 'startVehicleVisual',
            'def stop_vehicle_visual', 'stopVehicleVisual',
            'def drive_vehicle', 'notifyInputKeysDown'):
        if required not in bigworld_binding:
            errors.append(
                'bigworld_binding.py omits surveyed #1513 API: %s' %
                required)
    avatar_server = _text(os.path.join(
        package, 'entities', 'avatar_server.py'))
    move_mailbox = _block_at_any_indent(
        avatar_server, 'def', 'vehicle_moveWith') or ''
    for required in ('flags & 1', 'flags & 2', 'flags & 4', 'flags & 8',
                     'self._binding.drive_vehicle'):
        if required not in move_mailbox:
            errors.append(
                'avatar movement mailbox omits exact #1513 flag/API: %s' %
                required)
    for required in (
            'def _update_target_outline', 'wgAddEdgeDetectEntity',
            'wgDelEdgeDetectEntity', 'def _stop_remote_visual',
            'start_vehicle_visual', 'stop_vehicle_visual',
            'vehicle.appearance.changeVisibility(visible)'):
        if required not in battle_runtime:
            errors.append(
                'battle_runtime.py omits surveyed presentation API: %s' %
                required)
    if 'visibleAttachments' in battle_runtime:
        errors.append(
            'battle_runtime.py uses the absent #1513 visibleAttachments API')
    compat = _text(os.path.join(package, 'compat.py'))
    for required in (
            'def vehicle_setattr', "name in ('health', 'isCrewActive')",
            '_vehicle_property_overlays', 'def vehicle_get_speed',
            "overlay.get('_pose_active')", "'speed' in overlay"):
        if required not in compat:
            errors.append(
                'compat.py omits local server-property overlay: %s' %
                required)
    for required in ('_offlineLANShotYaw', '_offlineLANShotPitch'):
        if required not in remote_vehicle:
            errors.append('remote_vehicle.py omits dispersed tracer ray: %s' %
                          required)
    if "createEntity(\n+                'Vehicle'" in remote_vehicle:
        errors.append('remote_vehicle.py recreates the failed stock remote '
                      'Vehicle carrier')
    for forbidden in ('self.model.position =', 'self.model.yaw =',
                      'self.model.pitch =', 'self.model.roll =',
                      'visibleAttachments'):
        if forbidden in remote_vehicle:
            errors.append('remote_vehicle.py assumes PyCompoundModel exposes '
                          'ordinary Model pose attribute: %s' % forbidden)
    # PyCompoundModel is a native type and does not accept the ordinary Model
    # surface that 0.8.2 used.  Keep every direct production access on the
    # three members proven by the pinned #1513 CompoundAppearance and model
    # assembler bytecode.  A new member must first be added to the ABI audit
    # above with exact client evidence.
    compound_member_pattern = re.compile(
        r'\b(?:compoundModel|model|_local_model)\.([A-Za-z_]\w*)')
    compound_members = set()
    for directory, unused_names, names in os.walk(package):
        for name in names:
            if name.endswith('.py'):
                source = _text(os.path.join(directory, name))
                compound_members.update(
                    compound_member_pattern.findall(source))
    unexpected_members = sorted(
        compound_members.difference(('matrix', 'node', 'visible')))
    if unexpected_members:
        errors.append(
            '0.9.22 production code uses unverified PyCompoundModel '
            'members: %s' % ', '.join(unexpected_members))
    combat_rules = _text(os.path.join(package, 'combat_rules.py'))
    for required in ('def resolve_hull_hit', 'vehicleDamageFactor',
                     "kind == 'HOLLOW_CHARGE'", 'def he_nominal_armor'):
        if required not in combat_rules:
            errors.append('combat_rules.py omits 0.8.2 law: %s' % required)
    original_battle = _text(os.path.join(original, 'offline_battle.py'))
    for name in ('_offh_resolve_hull_hit', '_offh_is_he',
                 '_offh_he_radius', '_offh_he_hull_armor',
                 '_offh_he_nominal_armor', '_offh_he_damage',
                 '_offh_he_apply_tuning', '_offh_penetration'):
        copied_function = _top_level_function(combat_rules, name)
        if name == '_offh_he_hull_armor' and copied_function is not None:
            copied_function = copied_function.replace(
                "\t\t_hull = getattr(td, 'hull', None)\n"
                "\t\tif isinstance(_hull, dict):\n"
                "\t\t\tmats = _hull.get('materials') or {}\n"
                "\t\telse:\n"
                "\t\t\tmats = getattr(_hull, 'materials', None) or {}",
                "\t\tmats = (getattr(td, 'hull', None) or {}).get('materials') or {}")
        if copied_function != \
                _top_level_function(original_battle, name):
            errors.append(
                'combat_rules.py changed copied 0.8.2 function: %s' % name)
    device_damage = _text(os.path.join(package, 'device_damage.py'))
    original_device_damage = _text(os.path.join(
        original, 'device_damage.py'))
    device_functions = set(re.findall(
        r'^def ([A-Za-z_]\w*)\(', original_device_damage, re.MULTILINE))
    port_device_functions = set(re.findall(
        r'^def ([A-Za-z_]\w*)\(', device_damage, re.MULTILINE))
    if port_device_functions != device_functions | set(['_descriptor_value']):
        errors.append('device_damage.py changes the copied function inventory')
    for name in sorted(device_functions - set(['_misc_factor', '_raw_hp'])):
        if _top_level_function(device_damage, name) != \
                _top_level_function(original_device_damage, name):
            errors.append(
                'device_damage.py changed copied 0.8.2 function: %s' % name)
    for required in (
            'def _descriptor_value', 'if isinstance(value, dict)',
            'return getattr(value, name, default)',
            "comp = _descriptor_value(comp, sub)",
            "mh = _descriptor_value(comp, 'maxHealth')",
            "mrh = _descriptor_value(comp, 'maxRegenHealth', 0)"):
        if required not in device_damage:
            errors.append(
                'device_damage.py omits #1513 descriptor adapter: %s' %
                required)
    critical_damage = _text(os.path.join(package, 'critical_damage.py'))
    for kind, name in (
            ('class', '_SynthDeviceExtra'),
            ('class', '_SynthMaterial'),
            ('def', '_offh_interior_zone'),
            ('def', '_offh_voice_burst_pick'),
            ('def', '_offh_ignite'),
            ('def', '_offh_extinguish'),
            ('def', '_offh_knock_out_everything'),
            ('def', '_offh_module_test_mode'),
            ('def', '_offh_internal_layout'),
            ('def', '_offh_internal_ray_hits'),
            ('def', '_device_td'),
            ('def', '_crew_roster'),
            ('def', '_recompute_crew_impaired'),
            ('def', '_crew_factor'),
            ('def', '_module_factor'),
            ('def', '_knock_out_crew'),
            ('def', '_dev_destroyed_set'),
            ('def', '_module_ui_name'),
            ('def', '_refresh_mobility_flags'),
            ('def', '_apply_module_damage')):
        copied = _normalise_critical_proposal_guards(
            _normalise_1513_descriptor_access(
                _normalise_internal_imports(
                    _block_at_any_indent(critical_damage, kind, name) or '')))
        original_block = _block_at_any_indent(
            original_battle, kind, name)
        if copied != original_block:
            errors.append(
                'critical_damage.py changed copied 0.8.2 %s: %s' %
                (kind, name))
    for required in ('critical_damage.propose_direct',
                     'critical_damage.apply_payload',
                     'critical_damage.tick_repair',
                     'critical_damage.tick_fire',
                     'critical_damage.apply_drowning',
                     'critical_damage.apply_death',
                     'gun_mechanics.GunState',
                     'state.commit_fire',
                     'self._gun_state.scatter',
                     "critical_damage.stat_factor(entity, 'reload')",
                     'def _critical_proposal_contract',
                     "'critical_target_base_revision'",
                     "'critical_target_ack_seq'",
                     "'hull_damage'"):
        if required not in battle_runtime:
            errors.append('battle_runtime.py does not reuse %s' % required)
    if critical_damage.count(
            '_offh_knock_out_everything(vehicle, False)') != 2:
        errors.append(
            'critical_damage.py must keep copied death UI callbacks '
            'unreachable under the native #1513 adapter')
    for required in ('def use_extinguisher', 'def repair_device',
                     'def restore_crew'):
        if required not in critical_damage:
            errors.append('critical_damage.py omits 0.8.2 consumable law: %s' %
                          required)
    for required in ('def _tick_drowning',
                     'self._water_depth(self.local_pose()[0])',
                     'VEHICLE_DROWN_WARNING',
                     'self._drown_time <= 10.0'):
        if required not in battle_runtime:
            errors.append('battle_runtime.py omits 0.8.2 drowning path: %s' %
                          required)
    for required in ('combat_rules.he_splash_damage',
                     'def _he_splash', 'by_explosion=True',
                     'world_distance < 4999.5'):
        if required not in battle_runtime:
            errors.append('battle_runtime.py omits 0.8.2 HE path: %s' %
                          required)
    gun_mechanics = _text(os.path.join(package, 'gun_mechanics.py'))
    for required in (
            'weights = (0.6, 0.3, 0.1)',
            'crew_multiplier = 1.0 / (0.5 + 0.005 * 110.0)',
            'jump = self.base_dispersion * self.after_shot',
            'self.base_dispersion * 15.0',
            '1.0 + move_term * move_term +',
            'math.exp(-dt / max(aiming_time, 0.1))',
            'sigma = 0.0 if perfect_accuracy else dispersion / 3.0'):
        if required not in gun_mechanics:
            errors.append('gun_mechanics.py omits 0.8.2 inline law: %s' %
                          required)
    if gun_mechanics.count('gauss(0.0, sigma)') != 3:
        errors.append('gun_mechanics.py must preserve three-axis 0.8.2 scatter')
    for required in ('def _native_dispersion_angle',
                     'gun_rotator.dispersionAngle',
                     'dispersion_angle = self._native_dispersion_angle()',
                     'dispersion_angle=dispersion_angle',
                     'targeting_signature != self._targeting_signature'):
        if required not in battle_runtime:
            errors.append(
                'battle_runtime.py omits #1513 dispersion adapter: %s' %
                required)
    bot_shot = _block_at_any_indent(
        battle_runtime, 'def', '_resolve_bot_shot') or ''
    if 'except Exception' in bot_shot:
        errors.append(
            'bot shot collision path hides a #1513 native contract failure')
    if ('def _install_dispersion_override' in battle_runtime or
            'getOwnVehicleShotDispersionAngle = offline_dispersion' in
            battle_runtime):
        errors.append(
            'battle_runtime.py replaces the exact #1513 dispersion model')
    if 'rotator.dispersionAngle =' in battle_runtime:
        errors.append('battle_runtime.py writes read-only #1513 dispersion')
    for required in (
            'def _engine_rotation(yaw, pitch=0.0, roll=0.0)',
            'return (float(roll), float(pitch), float(yaw))',
            "'time_left': max(", "raw.get('time_left')",
            "'invaders': max(", "raw.get('invaders')",
            'captured(base_team, 0)'):
        if required not in battle_runtime:
            errors.append(
                'battle_runtime.py omits #1513 orientation/base adapter: %s' %
                required)
    for forbidden in ('(yaw, 0.0, 0.0)',
                      '(yaw, self._local_pitch, self._local_roll)',
                      'captured(1, base_team)'):
        if forbidden in battle_runtime:
            errors.append(
                'battle_runtime.py retains an incompatible 0.8.2 boundary: '
                '%s' % forbidden)
    compact_battle_runtime = ''.join(battle_runtime.split())
    base_points_call = (
        "callback(team,0,current['points'],current['time_left'],"
        "current['invaders'],current['stopped'])")
    if base_points_call not in compact_battle_runtime:
        errors.append(
            'battle_runtime.py does not call the #1513 base event with six '
            'ordered fields')
    if ("callback(team,0,current['points'],current['stopped'])" in
            compact_battle_runtime):
        errors.append(
            'battle_runtime.py retains the 0.8.2 four-field base event call')
    compatibility = _text(os.path.join(package, 'compat.py'))
    for required in ('def vehicle_leave_world',
                     'def compound_deactivate',
                     '_OfflineCameraColliderHandler',
                     '_OfflineEventSink', 'onCameraChanged',
                     "'onPeriodChange': _OfflineEventSink()"):
        if required not in compatibility:
            errors.append(
                'compat.py omits #1513 teardown adapter: %s' % required)
    destructibles_compat = _text(os.path.join(
        package, 'destructibles_compat.py'))
    for required in ('DestructiblesCache.encodeFallenTree',
                     'DestructiblesCache.encodeFallenColumn',
                     'DestructiblesCache.encodeDestructibleModule',
                     'DestructiblesCache.chunkIDFromPosition',
                     'DESTR_TYPE_FALLING_ATOM', 'DESTR_TYPE_STRUCTURE'):
        if required not in destructibles_compat:
            errors.append(
                'destructibles_compat.py omits #1513 moved API: %s' %
                required)
    if 'destructibles_compat.install(' not in battle_runtime:
        errors.append('battle_runtime.py does not install the #1513 '
                      'destructibles API adapter')
    destructibles_sensor = _text(os.path.join(
        package, 'destructibles_sensor.py'))
    copied_fell = _function_at_any_indent(
        destructibles_sensor, '_fell_trees_near')
    if copied_fell is not None:
        copied_fell = copied_fell.replace(
            '\n\timport BigWorld\n\timport Math', '')
    copied_try = _function_at_any_indent(
        destructibles_sensor, '_try_destroy_destructible')
    if copied_try is not None:
        copied_try = copied_try.replace(
            'def _try_destroy_destructible(spaceID, matInfo, yaw, vel,\n'
            '\t\tisShotDamage=False):',
            'def _try_destroy_destructible(spaceID, matInfo, yaw, vel):')
        copied_try = copied_try.replace(
            '\t\t\t_destr_ok = _auth.destroy_module(\n'
            '\t\t\t\tspaceID, chunkID, itemIndex, matKind, hitPt, '
            'isShotDamage)',
            '\t\t\t_destr_ok = _auth.destroy_module(spaceID, chunkID, '
            'itemIndex, matKind, hitPt, False)')
        report_block = (
            '\n\t\t\t_publish_destroyed(\n'
            "\t\t\t\t('tree' if typ == AreaDestructibles.DESTR_TYPE_TREE "
            'else\n'
            "\t\t\t\t 'column' if typ == "
            'AreaDestructibles.DESTR_TYPE_FALLING_ATOM else\n'
            "\t\t\t\t 'fragile' if typ == "
            'AreaDestructibles.DESTR_TYPE_FRAGILE else\n'
            "\t\t\t\t 'module'),\n"
            '\t\t\t\tchunkID, itemIndex, hitPt, yaw, vel,\n'
            '\t\t\t\tmatKind if typ == '
            'AreaDestructibles.DESTR_TYPE_STRUCTURE else None,\n'
            '\t\t\t\tisShotDamage)')
        copied_try = copied_try.replace(report_block, '')
    if copied_try != _function_at_any_indent(
            original_battle, '_try_destroy_destructible'):
        errors.append('destructibles_sensor.py changed copied 0.8.2 '
                      'function outside the LAN report seam: '
                      '_try_destroy_destructible')
    if _function_at_any_indent(
            destructibles_sensor, '_try_destroy_solid_hit') != \
            _function_at_any_indent(
                original_battle, '_try_destroy_solid_hit'):
        errors.append(
            'destructibles_sensor.py changed copied 0.8.2 function: '
            '_try_destroy_solid_hit')
    fell_report_block = (
        '\n\t\t\t\t\t_publish_destroyed(\n'
        "\t\t\t\t\t\t('fragile' if _ttyp == "
        'AreaDestructibles.DESTR_TYPE_FRAGILE\n'
        "\t\t\t\t\t\t else 'tree' if _ttyp == "
        'AreaDestructibles.DESTR_TYPE_TREE\n'
        "\t\t\t\t\t\t else 'column'),\n"
        '\t\t\t\t\t\tcid, _ti, pos, fall_yaw, vel)')
    if copied_fell is not None:
        copied_fell = copied_fell.replace(fell_report_block, '')
    if copied_fell != _function_at_any_indent(
            original_battle, '_fell_trees_near'):
        errors.append('destructibles_sensor.py changed copied 0.8.2 '
                      'function outside the LAN report seam: '
                      '_fell_trees_near')
    for required in ('def set_event_sink', 'def _publish_destroyed',
                     "'destructible_kind'", 'shot_yaw, 12.0, True'):
        if required not in destructibles_sensor:
            errors.append(
                'destructibles_sensor.py omits LAN destruction seam: %s' %
                required)
    world_collision = _text(os.path.join(package, 'world_collision.py'))
    for required in (
            'def _check_horizontal_collision',
            '_prev_y - _first_y > 0.15',
            'for offset_x in (-hw, 0, hw)',
            'col_bot = BigWorld.wg_collideSegment',
            'col_top = BigWorld.wg_collideSegment'):
        if required not in world_collision:
            errors.append('world_collision.py omits copied/version-local '
                          'wall boundary: %s' % required)
    for required in ('world_collision.check_horizontal_collision',
                     'shot_world_distance', "'maxDistance', 5000.0",
                     "event.get('shot_yaw')",
                     "event.get('shot_pitch')"):
        if required not in battle_runtime:
            errors.append('battle_runtime.py does not reuse %s' % required)
    lan_client = _text(os.path.join(package, 'lan_client.py'))
    for required in ("'battle_ready'", "'battle_live'", 'reported_health',
                     'message[\'_client_received_time\']',
                     'def _monotonic_time', 'self._combat_timing_tick',
                     'def _load_server_timing',
                     'def _attach_critical_proposal',
                     "'critical_target_base_revision'",
                     "'critical_target_ack_seq'", "'hull_damage'"):
        if required not in lan_client:
            errors.append('lan_client.py omits protocol boundary: %s' %
                          required)
    worker = _function_at_any_indent(lan_client, '_worker') or ''
    hello_send = worker.find('sock.sendall(payload)')
    connected_publish = worker.find('self.connected = True')
    if (hello_send < 0 or connected_publish < 0 or
            hello_send > connected_publish):
        errors.append('lan_client.py exposes connected before hello is sent')
    server = _text(os.path.join(repo_root, 'lan_battle_server.py'))
    for required in ('def mark_battle_ready', 'def _update_capture',
                     'def loading_snapshot', 'capture_bases',
                     'def _timing_payload', 'self.pending_live_message',
                     '"server_tick": self.tick',
                     'PREBATTLE_SECONDS = 15.0',
                     'RESULT_RESET_SECONDS = 5.0',
                     "state['invaders'] = invaders",
                     "state['time_left'] = (",
                     "state['stopped'] = defenders > 0",
                     '("shot", shot_seq, "player", target_id)',
                     '"bot_manifest": list(self.bot_manifest)',
                     'def _record_frag', '"kind": "vehicle_statistics"',
                     'death_attacker_kind',
                     'def _critical_proposal_admission',
                     'event["critical_accepted"]',
                     '"stale_target_state"', '"hull_damage"'):
        if required not in server:
            errors.append('lan_battle_server.py omits shared law: %s' %
                          required)
    for required in ('RAM_COOLDOWN_SECONDS = 0.75',
                     'self.bot_ram_cooldowns',
                     'shot_event["shot_yaw"]',
                     'shot_event["shot_pitch"]'):
        if required not in server:
            errors.append('lan_battle_server.py omits authority handoff law: '
                          '%s' % required)
    server_planner = _text(os.path.join(repo_root, 'server_bot_ai.py'))
    for required in ('class BotPlanner', 'def report_contacts',
                     'def report_affordances', 'def build_orders',
                     'score_candidates'):
        if required not in server_planner:
            errors.append('server_bot_ai.py omits documented macro boundary: %s' %
                          required)
    config = _text(os.path.join(package, 'config.py'))
    if "'prebattleCountdownSeconds': 15.0" not in config:
        errors.append('config.py default diverges from 0.8.2 countdown')
    if "'physics_tuning': {}" not in config:
        errors.append('config.py omits copied 0.8.2 physics tuning boundary')
    if "'he_tuning': {}" not in config:
        errors.append('config.py omits copied 0.8.2 HE tuning boundary')
    if "vehicle_physics.apply_tuning(self._config.get('physics_tuning'))" not in battle_runtime:
        errors.append('battle_runtime.py omits copied 0.8.2 tuning order')
    if "combat_rules.apply_he_tuning(self._config.get('he_tuning'))" not in battle_runtime:
        errors.append('battle_runtime.py omits copied 0.8.2 HE tuning order')
    build = _text(os.path.join(
        repo_root, 'ports', '0.9.22', 'build_wotmod.py'))
    if "'prebattleCountdownSeconds': 15.0" not in build:
        errors.append('build_wotmod.py release config diverges from 0.8.2 countdown')
    if "'physics_tuning': {}" not in build:
        errors.append('build_wotmod.py omits copied physics tuning config')
    if "'he_tuning': {}" not in build:
        errors.append('build_wotmod.py omits copied HE tuning config')
    if errors:
        for error in errors:
            print('ERROR: %s' % error)
        return 1
    print('Battle source audit passed: %d port modules documented; '
          '%d 0.8.2 modules classified; %d exact/normalized copies verified.' %
          (len(actual), len(source_actual),
           len(EXACT_COPIES) + len(WHITESPACE_COPIES) +
           len(PINNED_PORT_SHA256) + len(PINNED_SERVER_SHA256) + 4))
    return 0


if __name__ == '__main__':
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), '..', '..', '..')
    sys.exit(audit(base))
