#!/usr/bin/env python
"""Fail when a #1513 module has no documented 0.8.2/source provenance."""

from __future__ import print_function

import hashlib
import json
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
    'ai/maps.py': '082_import_adapter_plus_0922_data_and_review_overlay',
    'ai/maps_0922_extra.py': '0922_map_data',
    'ai/maps_extra.py': '082_whitespace_only',
    'ai/maps_group_a.py': '082_routes_plus_0922_resource_data',
    'ai/maps_group_b.py': '082_routes_plus_0922_resource_data',
    'ai/maps_group_c.py': '082_whitespace_only',
    'ai/navigation.py':
        '082_navigation_performance_plus_empty_penalty_and_shallow_water_guard',
    'ai/planner.py': '082_latest_law_plus_spawn_join',
    'ai/reviewed_routes_20260811.py': '0922_user_reviewed_route_data',
    'artillery_arc_queue.py': '1513_bounded_native_arc_probe_scheduler',
    'artillery_controller.py': '1513_exact_low_high_artillery_adapter',
    'ballistics.py': 'engine_free_elapsed_ballistics_law',
    'battle_feedback.py': '082_law_plus_1513_presenter',
    'battle_runtime.py': '1513_native_entity_adapter',
    'bootstrap.py': '1513_lifecycle_adapter',
    'bot_runtime.py':
        'server_authority_plus_082_lazy_pose_repair_fire_and_handoff_adapter',
    'combat_rules.py': '082_law_adapter',
    'critical_damage.py': '082_generated_closure_adapter',
    'compat.py': '1513_lifecycle_adapter',
    'config.py': '082_config_adapter',
    'destructibles_authority.py':
        '082_law_plus_1513_transaction_and_fragile_abi_adapter',
    'destructibles_compat.py': '1513_destructibles_api_adapter',
    'destructibles_sensor.py': '082_contact_law_plus_strict_1513_adapter',
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
    'lobby_ui.py': '1513_lobby_presentation_adapter',
    'internal_geometry.py': '082_law_plus_1513_descriptor_adapter',
    'internal_hit_layouts.py': '082_import_plus_1513_descriptor_adapter',
    'internal_layout_profiles.py': 'exact_082',
    'internal_layout_store.py': '082_import_adapter',
    'map_catalog.py': '1513_arena_api',
    'navigation_graph_schema.py': '1513_navigation_release_contract',
    'prebaked_foliage.py': '082_foliage_data_adapter',
    'prebaked_destructibles.py':
        '1513_compiled_destructible_contact_catalog_adapter',
    'prebaked_navigation.py': '082_navigation_graph_adapter',
    'projectile_manager.py': 'bounded_projectile_lifetime_owner',
    'projectile_runtime.py': 'engine_free_swept_projectile_law',
    'queue_ui.py': '1513_scaleform_adapter',
    'snapshot_sync.py': '082_protocol_adapter',
    'spawn_planner.py': '082_law_plus_1513_arena_data',
    'spotting.py': '082_law_plus_1513_descriptor_data',
    'tank_collision.py': '082_current_collision_and_spatial_index_port',
    'user_config.py': '1513_path_adapter',
    'vehicle_physics.py': '082_latest_calibrated_law_port',
    'waiting_room_ui.py': '082_waiting_room_law_plus_1513_native_gui_adapter',
    'world_collision.py': '082_law_plus_strict_destructible_adapter',
}
EXPECTED_PORT_MODULE_COUNT = 65

# The #1513 battle also depends on two Python 3 service modules outside the
# wotmod. They live with the port and are part of the same provenance gate
# because they own shared room/rule/macro state, not deployment-only
# infrastructure.
SERVER_FILES = {
    'server/lan_battle_server.py': '082_protocol_and_shared_law_adapter',
    'server/server_bot_ai.py': '082_current_server_macro_planner',
}

# The working 0.8.2 implementation was finalized in a separate release
# worktree.  A public release cannot depend on that local checkout, so adapted
# modules are pinned here after source review. Most remain based on commit
# 7e3a1b2; the five performance/control files below include the reviewed
# 2026-08-08 release-worktree delta measured on the legacy client. This makes
# subsequent drift fail deterministically instead of silently comparing
# against the older source left in this repository's main checkout.
# ``bot_runtime.py`` additionally contains the documented #1513-only seam
# that presents copied poses each render frame but forms LAN publications at
# the nominal 30 Hz protocol cadence so strict combat sequences are not lost.
FINAL_082_BASELINE = '7e3a1b2969e839b8e00acab0a0e3bc39f8bcc48c'
PINNED_PRODUCT_SHA256 = {
    'bootstrap.py':
        'c634fa2d0bf674cc6d32322161c9259344cdd42e63abf989c2f453770c013610',
    'config.py':
        '5581dff4f35a2968f97ec4dcabc35c692326a020db20a1eaa33c075d2707c70b',
    'lan_session.py':
        'e618c793fc47a6080453371a4444a18e1b96ea44c074be44a97d6a1a73db9c66',
    'lobby_ui.py':
        '5d52c57da8b5b131ace99ed16a19a052df40e125eefd24a86e5830af0bccbb54',
}
PINNED_PORT_SHA256 = {
    'ai/adapter.py': '7abfd2af9a9e1f39a5fe7143ef729af4431430b06f3a6d0bac4e669449ea00f4',
    'ai/driver.py': '5b7c1588fefb274e823b4a043277209ac47eab23ecabe277b9669021472fe02a',
    'ai/maps.py': '7f2b01b268ace18dca2d6f13a038aef908ee785caa8dccb9d4b66e5a6bd63525',
    'ai/maps_group_a.py': '35a7d8b34c1c78f889bbe30d54d7898fd581259769d1aa13112eda3f32afcc7f',
    'ai/maps_group_b.py': '152aae3d25921998b9436c3fbe2210d6f640c92220cf1729504fd59ce7b952d1',
    'ai/navigation.py': '7aabe5283fd46ab02a30e772be25637661ef6d2e7564640b0226227b6dff3c69',
    'ai/planner.py': '1c7d97f42fd804364e16bd5c1fe5ef86a48a55390bf7c938c5c313d96a50bf92',
    'ai/reviewed_routes_20260811.py': 'dba5ea626d913619467f78cf594c7c990bc0867abf430f5fe9c5519c09e9331a',
    'battle_runtime.py': '96405ad0dace41ce05378cb1163aee91d78bc2306e5baed6b3c499b8f56a2045',
    'bot_runtime.py': '016d27b79a03f56e6ba4f81d514e6c49c1e9aa7b0af9494c8af825771c15c0c7',
    'combat_rules.py': '9dd0296fe2a9e9340de3608f8017f6270e8f8e2d0d6ce9f673edecd2e1ce75bf',
    'entities/remote_vehicle.py': '0978c6f2715f91f82f505de8ae85696940736a30e4bb8733bc7b4c0debecd51b',
    'lan_client.py': '0d60a3af33a70876d3c0aaae58ba9052584bade177c692e5c98f0b2a3fa2a8c6',
    'snapshot_sync.py': '8b3bfd569d225adc2308c095800c9caf6d2f3b057e362a88dc45bc94b5bd756f',
    'tank_collision.py': '1b8deca1d273e1cc18445a2454222aa23bac3a712cc836feb179a1fa2f8dc0ef',
    'vehicle_physics.py': '97e0411ec332a4c47624316fee88775ce5564942b1851dc2f6b63831fa3a3d26',
    'destructibles_authority.py': 'efe866862fbe2e2e996a63139f14ba471b7487a575911a327e6ddab98125105f',
    'destructibles_sensor.py': '6fb0423388eea6919d9a8994e51866e4f83fd26afb098300e6a69e4420e41ea0',
    'projectile_manager.py': '5fa806ea34d6256f0b84d26e24dbcf5fc435b289d49b42d3ea5288014b1ac6a3',
    'projectile_runtime.py': 'adf7b8f1ffc2d1ef926ac04ad835501c1cdc279dcee1d6792b596e902da9573e',
    'prebaked_destructibles.py': '48fe24ceae4ea41692590916948312c4d484a4bed0d8bec729ef29386bb2fcc3',
    'world_collision.py': 'f5506a4cf29e2d6a1eb7bfd2400f1703aeae81fad02392d61a50d618198dd925',
}
PINNED_SERVER_SHA256 = {
    'server/lan_battle_server.py': '5df02ad81456d30cf96bbf5b76f287ee2b2709850b887e8ffac78eda18ae4e16',
    'server/server_bot_ai.py': '306916334e492297f653ad024bf9004d281ed4142a5fd6afa4155b135a9220de',
    'server/windows_server.py': 'a7a0c0a066a69539e86aa9c3f75f52a734ff7a188709022b342ab1b7f7988aae',
}
PINNED_RELEASE_SHA256 = {
    'tools/bake_destructibles_0922.py':
        'b7b9e22b11b6712c26e52e9c43c741fd0fbf19c5bd7a31cfa749a74cc8a70b9a',
    'destructibles/manifest.json':
        '8df129937397eaf42fc92ea357cc21b7babd60e1031cf9a13f96907977684852',
}

# Every Python file in the frozen reviewed 0.8.2 source manifest is classified
# here. Live 0.8.2 development is intentionally not a 0.9.22 release gate;
# importing a newer law requires a separate parity review and manifest update.
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
    'internal_geometry.py': 'internal_geometry.py',
    'internal_layout_profiles.py': 'internal_layout_profiles.py',
}

WHITESPACE_COPIES = {
    'ai/maps_extra.py': 'bot_ai_maps_extra.py',
    'ai/maps_group_c.py': 'bot_ai_maps_group_c.py',
}

REVIEWED_PORT_CONTRACT_FILES = set(EXACT_COPIES) | set(WHITESPACE_COPIES) | {
    'internal_hit_layouts.py',
    'internal_layout_store.py',
    'combat_rules.py',
    'device_damage.py',
    'critical_damage.py',
}


def _read(path):
    with open(path, 'rb') as stream:
        return stream.read()


def _sha256(path):
    return hashlib.sha256(_read(path)).hexdigest()


def _text(path):
    value = _read(path)
    if not isinstance(value, str) or sys.version_info[0] < 3:
        value = value.decode('utf-8')
    return value


def _normalise_newlines(value):
    return value.replace('\r\n', '\n').replace('\r', '\n')


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
    return _block_at_any_indent(value, 'def', name + '(')


def audit(repo_root):
    repo_root = os.path.abspath(repo_root)
    port_root = os.path.join(repo_root, '0.9.22')
    package = os.path.join(
        repo_root, '0.9.22', 'src', 'res', 'scripts', 'client',
        'gui', 'mods', 'offline_lan_0922')
    actual = set()
    for root, unused_dirs, files in os.walk(package):
        for name in files:
            if name.endswith('.py'):
                actual.add(os.path.relpath(os.path.join(root, name), package))
    documented = set(PORT_FILES)
    errors = []
    if not os.path.isdir(port_root):
        errors.append('top-level 0.9.22 release directory is missing')
    retired_port_root = os.path.join(repo_root, 'ports', '0.9.22')
    if os.path.exists(retired_port_root):
        errors.append('retired nested release directory still exists')
    if len(actual) != EXPECTED_PORT_MODULE_COUNT:
        errors.append('expected %d production port modules, found %d' %
                      (EXPECTED_PORT_MODULE_COUNT, len(actual)))
    for missing in sorted(actual - documented):
        errors.append('undocumented port module: %s' % missing)
    for stale in sorted(documented - actual):
        errors.append('documented module is missing: %s' % stale)
    manifest_path = os.path.join(
        port_root, 'tools', 'reviewed_082_source_manifest.json')
    try:
        manifest = json.loads(_text(manifest_path))
        reviewed_source_files = manifest['source_files']
        reviewed_port_contracts = manifest['reviewed_port_contracts']
        reviewed_baseline = manifest['baseline_commit']
    except (IOError, KeyError, TypeError, ValueError) as error:
        print('ERROR: invalid reviewed 0.8.2 source manifest: %s' % error)
        return 1
    source_actual = set(reviewed_source_files)
    source_documented = set(SOURCE_FILE_CLASSES)
    for missing in sorted(source_actual - source_documented):
        errors.append('unclassified 0.8.2 source module: %s' % missing)
    for stale in sorted(source_documented - source_actual):
        errors.append('classified 0.8.2 source module is missing: %s' % stale)
    if reviewed_baseline != '0a96d75978dd7160c3392e5b1089bdbc07b9bd8b':
        errors.append('reviewed 0.8.2 source baseline is not the approved '
                      '0.9.22 release checkpoint')
    for source_name, digest in sorted(reviewed_source_files.items()):
        if not re.match(r'^[0-9a-f]{64}$', digest):
            errors.append('reviewed 0.8.2 source digest is invalid: %s' %
                          source_name)
    if set(reviewed_port_contracts) != REVIEWED_PORT_CONTRACT_FILES:
        errors.append('reviewed 0.8.2 port contract inventory is incomplete')
    for port_name, expected in sorted(reviewed_port_contracts.items()):
        if not re.match(r'^[0-9a-f]{64}$', expected):
            errors.append('reviewed port contract digest is invalid: %s' %
                          port_name)
            continue
        if _sha256(os.path.join(package, port_name)) != expected:
            errors.append('%s diverged from the frozen reviewed 0.8.2 '
                          'contract' % port_name)
    audit_document = _text(os.path.join(
        repo_root, '0.9.22', 'BATTLE_SOURCE_AUDIT.md'))
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
        if not os.path.isfile(os.path.join(port_root, server_name)):
            errors.append('documented battle service is missing: %s' %
                          server_name)
        if '`%s`' % server_name not in audit_document:
            errors.append('battle service lacks a written explanation: %s' %
                          server_name)
    for source_name in sorted(source_actual):
        if '`%s`' % source_name not in audit_document:
            errors.append('0.8.2 module lacks a written disposition: %s' %
                          source_name)
    for port_name, expected in sorted(PINNED_PORT_SHA256.items()):
        if _sha256(os.path.join(package, port_name)) != expected:
            errors.append('%s diverged from reviewed final 0.8.2 port %s' %
                          (port_name, FINAL_082_BASELINE[:7]))
    for product_name, expected in sorted(PINNED_PRODUCT_SHA256.items()):
        if _sha256(os.path.join(package, product_name)) != expected:
            errors.append('%s diverged from the frozen 0.4.0 product seam' %
                          product_name)
    for server_name, expected in sorted(PINNED_SERVER_SHA256.items()):
        if _sha256(os.path.join(port_root, server_name)) != expected:
            errors.append('%s diverged from reviewed final 0.8.2 service %s' %
                          (server_name, FINAL_082_BASELINE[:7]))
    for release_name, expected in sorted(PINNED_RELEASE_SHA256.items()):
        if _sha256(os.path.join(port_root, release_name)) != expected:
            errors.append('%s diverged from the reviewed #1513 release data' %
                          release_name)
    try:
        destructible_manifest = json.loads(_text(os.path.join(
            port_root, 'destructibles', 'manifest.json')))
        destructible_census = destructible_manifest['census']
        if (int(destructible_manifest.get('version', -1)) != 3 or
                int(destructible_census.get('maps', -1)) != 41 or
                int(destructible_census.get('instance_signatures', -1)) !=
                61625 or
                int(destructible_census.get(
                    'ambiguous_instance_signatures', -1)) != 11 or
                int(destructible_census.get(
                    'ambiguous_instance_candidates', -1)) != 28):
            errors.append('destructible schema-v3 release census is invalid')
    except (IOError, KeyError, TypeError, ValueError):
        errors.append('destructible schema-v3 release manifest is invalid')
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
                     'def _pending_event_references',
                     'def on_bot_observation',
                     'def _spectator_record',
                     'def _switch_postmortem_viewpoint',
                     'def _fallback_postmortem_viewpoint',
                     'self._fallback_postmortem_viewpoint(engine_id)',
                     'self._release_postmortem_visibility()'):
        if required not in battle_runtime:
            errors.append('battle_runtime.py does not reuse %s' % required)
    if "_field(shell, 'piercingPower'" in battle_runtime:
        errors.append('battle_runtime.py reads #1513 penetration from shell, not shot')
    if 'BOT_VEHICLE_CANDIDATES' in battle_runtime:
        errors.append('battle_runtime.py hard-codes a replacement bot lineup')
    for required in (
            "'wg_setSpaceItemsVisibilityMask'",
            'set_mask(space_id, expected)',
            'lobby_boundary = self._preflight_lobby_retirement()',
            'self._enter_battle_loading()',
            'self._retire_lobby_entities(lobby_boundary)'):
        if required not in battle_runtime:
            errors.append('battle_runtime.py omits #1513 space/lobby '
                          'lifecycle boundary: %s' % required)
    for forbidden in ('wg_getSpaceItemsVisibilityMask',
                      'space.itemsVisibilityMask',
                      'spaces[self._avatar.spaceID]'):
        if forbidden in battle_runtime:
            errors.append('battle_runtime.py requires a synchronous #1513 '
                          'SpaceData visibility readback: %s' % forbidden)
    configure_visibility = _function_at_any_indent(
        battle_runtime, '_configure_standard_space_visibility') or ''
    for required in (
            'gameplay_id = int(self._arena_type.gameplayID)',
            'selected_bit = gameplay_mask(gameplay_id)',
            'expected = selected_bit',
            'set_mask(space_id, expected)',
            'return expected'):
        if required not in configure_visibility:
            errors.append('battle_runtime.py selected-gameplay visibility '
                          'seam omits %s' % required)
    finish_entity_startup = _function_at_any_indent(
        battle_runtime, '_finish_entity_startup') or ''
    late_visibility_index = finish_entity_startup.find(
        'self._configure_standard_space_visibility()')
    ready_index = finish_entity_startup.find("record['ready'] = True")
    if (late_visibility_index < 0 or ready_index < 0 or
            late_visibility_index > ready_index):
        errors.append('battle_runtime.py does not restore the selected CTF '
                      'visibility bit after late stock client readiness')
    if battle_runtime.count(
            'self._configure_standard_space_visibility()') != 2:
        errors.append('battle_runtime.py must apply selected gameplay '
                      'visibility once at space creation and once after '
                      'deferred client readiness')
    loading_index = battle_runtime.find('self._enter_battle_loading()')
    retirement_index = battle_runtime.find(
        'self._retire_lobby_entities(lobby_boundary)')
    map_index = battle_runtime.find('self._map_create_attempted = True')
    if not (0 <= loading_index < retirement_index < map_index):
        errors.append('battle_runtime.py does not dispose the lobby before '
                      'Account retirement and map creation')
    for required in ('_spawn_cache', '_formation_pose',
                     'notifyInputKeysDown', 'RemoteVehicleFactory',
                     'self._remote_factory.prepare_descriptor(descriptor)',
                     'native_motion=False', 'set_vehicle_pose',
                     'set_vehicle_pose_overlay', '_update_local_presentation',
                     'model.matrix = self._local_matrix',
                     'zero_motion, zero_motion',
                     'velocity, acceleration)',
                     'FRAME_SECONDS = 0.0', 'presentation_states()',
                     '_update_spotting', 'spot_until',
                     'event.pop(\'pose\', None)'):
        if required not in battle_runtime:
            errors.append('battle_runtime.py omits #1513 movement/spawn '
                          'boundary: %s' % required)
    local_presentation = _function_at_any_indent(
        battle_runtime, '_update_local_presentation') or ''
    if 'self._local_model.matrix' in local_presentation:
        errors.append('battle_runtime.py polls or rebinds the native compound '
                      'matrix on the render cadence')
    if 'entity.teleport(' in battle_runtime:
        errors.append('battle_runtime.py calls forbidden client-side '
                      'Entity.teleport')
    if 'authority_entity_resolver=self._server_entity' not in battle_runtime:
        errors.append('battle_runtime.py does not wire the private authority '
                      'entity resolver')
    for required in (
            'align_camera = getattr(camera, \'setToVehicleDirection\'',
            'reset_rotator = getattr(rotator, \'reset\', None)',
            'reset_rotator()',
            'def _publish_targeting_info',
            'self._echo_local_gun_angles(0.0, 0.0)',
            'align_sender(0.0, 0.0)',
            'def _authority_players', "'world_pose': True",
            'def _present_direct_spot', 'event_types.SPOTTED',
            'event_types.TARGET_VISIBILITY',
            'pack_visibility(True, True)', "'spot_feedback_sent'"):
        if required not in battle_runtime:
            errors.append('battle_runtime.py omits #1513 initial-aim or '
                          'spotting-feedback boundary: %s' % required)
    for forbidden in (
            'def _start_prebattle_gun_tracking',
            'def _enable_prebattle_camera_controls',
            '_AvatarInputHandler__onArenaStarted',
            "getattr(rotator, 'start'",
            "setattr(self._avatar, arena_name, True)"):
        if forbidden in battle_runtime:
            errors.append('battle_runtime.py bypasses the stock PREBATTLE '
                          'gun/reticle freeze: %s' % forbidden)
    begin_battle = _function_at_any_indent(
        battle_runtime, '_begin_battle') or ''
    battle_period = "self._binding.arena_period('battle', duration)"
    battle_live = 'self._battle_live = True'
    if battle_period not in begin_battle or battle_live not in begin_battle:
        errors.append('battle_runtime.py omits the native BATTLE transition')
    elif begin_battle.find(battle_period) > begin_battle.find(battle_live):
        errors.append('battle_runtime.py opens gameplay before the native '
                      'BATTLE transition')
    if battle_runtime.count(battle_period) != 1:
        errors.append('battle_runtime.py must publish exactly one native '
                      'BATTLE-period transition path')
    prebattle_live = _function_at_any_indent(
        battle_runtime, 'on_battle_live') or ''
    for forbidden in ('_sync_local_server_marker(',
                      '_publish_targeting_info(',
                      '_begin_battle('):
        if forbidden in prebattle_live and not (
                forbidden == '_begin_battle(' and
                'if countdown <= 0.0:' in prebattle_live):
            errors.append('battle_runtime.py PREBATTLE setup drives aiming: '
                          '%s' % forbidden)
    server_marker = _function_at_any_indent(
        battle_runtime, '_sync_local_server_marker') or ''
    marker_fence = 'if not self._battle_live:'
    rotator_lookup = "getattr(self._avatar, 'gunRotator', None)"
    if marker_fence not in server_marker:
        errors.append('battle_runtime.py server marker has no BATTLE fence')
    elif (rotator_lookup not in server_marker or
          server_marker.find(marker_fence) > server_marker.find(rotator_lookup)):
        errors.append('battle_runtime.py reads the server marker before its '
                      'BATTLE fence')
    targeting_info = _function_at_any_indent(
        battle_runtime, '_publish_targeting_info') or ''
    for required in (
            'if entity is None:',
            'entity = self._server_entity(self._server.vehicle_id)',
            'if state is None:', 'state = self._gun_state',
            'self._avatar.updateTargetingInfo(',
            'self._targeting_signature = targeting_signature'):
        if required not in targeting_info:
            errors.append('battle_runtime.py targeting-info seam omits %s' %
                          required)
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
    if '_observe_local_vehicle(outgoing' in battle_runtime:
        errors.append('battle_runtime.py consumes authority observation '
                      'before the server relay')
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
                     'handoff_canonical_reset', 'PUBLICATION_SECONDS',
                     'def presentation_states',
                     'OBSERVATION_SECONDS = 0.40',
                     'SHOT_LANE_SECONDS = 0.20',
                     'SHOT_LANE_REFRESH_SECONDS = 0.20',
                     'SHOT_LANE_QUERY_DISTANCE = 585.0',
                     'OBSERVATION_SECONDS - SHOT_LANE_REFRESH_SECONDS',
                     'def _refresh_shot_clear',
                     'def _index_live_players',
                     'def _refresh_target_pose',
                     'refresh_all_targets = bool(',
                     "sync['unpublished_steps'] = list(replay_steps)",
                     'def _pack_observations',
                     'lane_key=lane_key',
                     'distance_cache=distance_cache',
                     'self._gun_yaw_limits',
                     'spotting.effective_camouflage',
                     'spotting.is_detected',
                     'def _detection_upper_bound',
                     "visibility.get('foliage_bonus')",
                     "publish and command['fire_allowed']",
                     'def probe_duration_totals',
                     'self._probe_duration_totals',
                     'if centre is not None:',
                     'return centre, centre'):
        if required not in bot_runtime:
            errors.append('bot_runtime.py does not reuse %s' % required)
    shot_los_phase = _function_at_any_indent(
        bot_runtime, '_shot_los_phase') or ''
    if 'SHOT_LANE_REFRESH_SECONDS' not in shot_los_phase:
        errors.append('bot_runtime.py does not phase ordinary lane refreshes '
                      'through the 0.20-second refresh window')
    shot_clear = _function_at_any_indent(bot_runtime, '_shot_clear') or ''
    for required in (
            'else SHOT_LANE_QUERY_DISTANCE)',
            'if target_distance > query_distance:',
            '_number(now) - cached[0] <= SHOT_LANE_SECONDS + 1e-9',
            'self.firing_lane_probe(source, target)'):
        if required not in shot_clear:
            errors.append('bot_runtime.py final-fire/query-distance lane '
                          'seam omits %s' % required)
    refresh_shot_clear = _function_at_any_indent(
        bot_runtime, '_refresh_shot_clear') or ''
    for required in (
            'window_start = observation_time - SHOT_LANE_REFRESH_SECONDS',
            'deadline = window_start + self._shot_los_phase(key)',
            'source, target, now, force=True, probe_budget=probe_budget',
            'self._shot_los_deadlines[key] = observation_time'):
        if required not in refresh_shot_clear:
            errors.append('bot_runtime.py phased observation-lane refresh '
                          'omits %s' % required)
    for required in (
            'def _bot_ammo_capacity',
            "maximum = _value(descriptor, 'maxAmmo', None)",
            'def _bot_ammo_distribution',
            "if class_tag == 'SPG'",
            "{'standard': 3.0, 'premium': 2.0, 'he': 1.0}",
            'class _BotAmmoState(object):',
            'self.remaining[self.loaded] -= 1',
            "state['ammo_reload_pending'] = bool(self.reload_pending)"):
        if required not in bot_runtime:
            errors.append('bot_runtime.py finite-ammunition boundary omits %s' %
                          required)
    ammo_state = _block_at_any_indent(
        bot_runtime, 'class', '_BotAmmoState') or ''
    for required in (
            'self.reload_pending = reload_pending',
            'if self.reload_pending:',
            'selected = self._available(self.next)',
            'self.loaded = selected',
            'self.reload_pending = False',
            'self.reload_pending = True'):
        if required not in ammo_state:
            errors.append('bot_runtime.py loaded/next reload boundary omits %s' %
                          required)
    human_contacts = _function_at_any_indent(
        bot_runtime, '_contacts_for') or ''
    for required in (
            'vehicle_profile = self._player_vehicle_profile(raw)',
            "target['class_tag'] = vehicle_profile['class_tag']",
            "target['armor'] = vehicle_profile['armor']"):
        if required not in human_contacts:
            errors.append('bot_runtime.py human target profile seam omits %s' %
                          required)
    player_profile = _function_at_any_indent(
        bot_runtime, '_player_vehicle_profile') or ''
    for required in (
            "cache_key = vehicle_name or ''",
            'cached = self._player_vehicle_profiles.get(cache_key)',
            'descriptor = self.descriptor_resolver(',
            'tactical = ai_planner.build_vehicle_profile(descriptor)',
            "'armor': max(0.0, _number(tactical.get('armor')))",
            'self._player_vehicle_profiles[cache_key] = cached'):
        if required not in player_profile:
            errors.append('bot_runtime.py immutable human descriptor profile '
                          'omits %s' % required)
    if "raw.get('armor')" in player_profile:
        errors.append('bot_runtime.py accepts mutable human armor instead of '
                      'the installed descriptor profile')
    overlay_target = _function_at_any_indent(
        bot_runtime, '_overlay_target_state') or ''
    for required in (
            "for name in ('alive', 'health', 'max_health', 'team',",
            "'x', 'y', 'z', 'yaw', 'speed'):"):
        if required not in overlay_target:
            errors.append('bot_runtime.py live target overlay may mutate the '
                          'immutable armor/class profile: %s' % required)
    navigation = _text(os.path.join(package, 'ai', 'navigation.py'))
    for required in ('def _baked_clearance_penalty',
                     'def shortcut_preserves_baked_clearance',
                     'def _prefers_baked_clearance',
                     'prefer_clearance=self._prefers_baked_clearance(path_key)'):
        if required not in navigation:
            errors.append('ai/navigation.py omits bounded shared-route '
                          'clearance boundary: %s' % required)
    hull_dimensions = _function_at_any_indent(
        bot_runtime, '_hull_dimensions') or ''
    if 'tank_collision.chassis_shape(descriptor)' not in hull_dimensions:
        errors.append('bot_runtime.py does not derive AI dimensions from '
                      'the admitted #1513 collision body')
    for forbidden in ('half_length = 3.5', 'half_width = 1.7'):
        if forbidden in hull_dimensions:
            errors.append('bot_runtime.py retains silent collision geometry '
                          'fallback: %s' % forbidden)
    for forbidden in ('maximum_turn = 0.85', "'affordances': []"):
        if forbidden in bot_runtime:
            errors.append('bot_runtime.py retains replacement law: %s' %
                          forbidden)
    if 'def apply_ground(' in bot_runtime:
        errors.append('bot_runtime.py retains the removed historical '
                      'ground-snap helper')
    if "result['world_pose'] = True" not in bot_runtime:
        errors.append('bot_runtime.py can resolve one spawn slot twice')
    realised_invalidation = _function_at_any_indent(
        bot_runtime, '_invalidate_realised_motion') or ''
    for required in (
            'self._decision_cache.pop(bot_id, None)',
            'self._motion_probe_cache.pop(bot_id, None)',
            "getattr(driver, 'remember_failure', None)",
            'remember(bot_id, attempted_yaw, 5.0)'):
        if required not in realised_invalidation:
            errors.append('bot_runtime.py realised-motion invalidation omits '
                          '%s' % required)
    vertical_motion = _function_at_any_indent(
        bot_runtime, '_update_vertical_motion') or ''
    for required in (
            'tank_collision.support_rise_is_obstacle(',
            "state['x'], state['y'], state['z'] = tick_pose",
            "state['speed'] = 0.0",
            'self._invalidate_realised_motion('):
        if required not in vertical_motion:
            errors.append('bot_runtime.py support-rise rollback omits %s' %
                          required)
    first_placement = vertical_motion.find(
        "if not state.get('grounded_once', False):")
    support_reject = vertical_motion.find(
        'elif tank_collision.support_rise_is_obstacle(')
    if (first_placement < 0 or support_reject < 0 or
            first_placement > support_reject):
        errors.append('bot_runtime.py applies support-rise rejection before '
                      'first terrain placement')
    realised_guard = _function_at_any_indent(
        bot_runtime, '_guard_realised_pose') or ''
    if 'self._invalidate_realised_motion(state[\'id\'], attempted_yaw)' not in \
            realised_guard:
        errors.append('bot_runtime.py realised-pose rollback retains stale '
                      'decision or motion proof')
    bot_visibility = _function_at_any_indent(
        battle_runtime, '_bot_visibility') or ''
    for required in ("'line_of_sight'", "'foliage_bonus'",
                     'self._foliage.camouflage_bonus('):
        if required not in bot_visibility:
            errors.append('battle_runtime.py bot spotting omits %s' % required)
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
                     'def prepare_descriptor', 'loadBspModel',
                     'releaseBspModel', "getattr(tester, 'bbox', None)",
                     'self._descriptors',
                     'tank_collision.forget_chassis_shape(descriptor)',
                     "'OfflineEntity'", 'prepareCompoundAssembler',
                     'loadResourceListBG', 'def set_pose',
                     'def collideSegmentExt', 'collide_vehicle_at_matrix',
                     'ProjectileMover', 'setupTurretRotations',
                     'assembleRecoil', 'extrasDict',
                     'self.model.matrix = self.matrix',
                     '_SegmentCollisionResult',
                     '_SegmentCollisionResultExt', 'compName'):
        if required not in remote_vehicle:
            errors.append('remote_vehicle.py omits copied carrier boundary: '
                          '%s' % required)
    for required in ('self._postmortem_visible = False',
                     'def _postmortem_visible',
                     '_postmortem_visible(vehicle)'):
        if required not in remote_vehicle:
            errors.append('remote_vehicle.py omits bounded postmortem AOI: %s' %
                          required)
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
    viewpoint_mailbox = _block_at_any_indent(
        avatar_server, 'def', 'switchViewPointOrBindToVehicle') or ''
    for required in ('callable(self._on_viewpoint_switch)',
                     'self._on_viewpoint_switch(',
                     'bool(is_viewpoint), int(vehicle_or_point_id)'):
        if required not in viewpoint_mailbox:
            errors.append('avatar viewpoint mailbox omits local server-style '
                          'attachment: %s' % required)
    move_mailbox = _block_at_any_indent(
        avatar_server, 'def', 'vehicle_moveWith') or ''
    for required in ("self._send_input('move', {'flags': flags})",):
        if required not in move_mailbox:
            errors.append(
                'avatar movement mailbox omits exact #1513 flag/API: %s' %
                required)
    if 'self._binding.drive_vehicle' in move_mailbox:
        errors.append('avatar movement mailbox duplicates the native '
                      'PlayerAvatar filter notification')
    setting_mailbox = _block_at_any_indent(
        avatar_server, 'def', 'vehicle_changeSetting') or ''
    for required in (
            'handler(self._vehicle_id, code, value)',
            'return',
            'updater(self._vehicle_id, code, value)'):
        if required not in setting_mailbox:
            errors.append(
                'avatar setting mailbox omits handled/native boundary: %s' %
                required)
    if not re.search(
            r'if\s*\(callable\(handler\)\s+and\s+'
            r'handler\(self\._vehicle_id,\s*code,\s*value\)\)\s*:\s*return',
            setting_mailbox):
        errors.append('avatar setting mailbox does not stop a truthy handled '
                      'request before native updateVehicleSetting')
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
            "overlay.get('_pose_active')", "'speed' in overlay",
            '_camera_acceleration_update_code',
            '_arcade_oscillator_acceleration_code',
            '_sniper_oscillator_acceleration_code',
            "name == 'vehicle'", "runtime.bigworld.entity(vehicle_id)",
            "overlay.get('velocity')", "overlay.get('acceleration')"):
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
    device_damage = _text(os.path.join(package, 'device_damage.py'))
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
                     'if targeting_signature == self._targeting_signature:'):
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
                     'DestructiblesCache.encodeFragile',
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
    prebaked_destructibles = _text(os.path.join(
        package, 'prebaked_destructibles.py'))
    destructibles_authority = _text(os.path.join(
        package, 'destructibles_authority.py'))
    for required in ('AreaDestructibles.encodeFragile(',
                     'bool(syncWithProjectile)',
                     'applyShotImmediately=False',
                     'if applyShotImmediately:',
                     'orderDestructibleDestroy(',
                     'chunkID, dmgTypes[kind], destrData, True, False)',
                     '(itemIndex, None), False, bool(isShotDamage)',
                     '(itemIndex, matKind), False, bool(isShotDamage)',
                     'pitchConstr, _collisionFlags = pc',
                     '#1513 destructible fall-pitch payload must contain 2 items',
                     'destructible controller rollback is unsafe',
                     "c[prop].append(destrData)",
                     "c['keys'].add(dedupKey)"):
        if required not in destructibles_authority:
            errors.append(
                'destructibles_authority.py omits strict #1513 boundary: %s' %
                required)
    if ('Fragiles take the RAW item index' in destructibles_authority or
            'if pc is not None' in destructibles_authority or
            destructibles_authority.find("c['keys'].add(dedupKey)") <
            destructibles_authority.find(
                'orderDestructibleDestroy(')):
        errors.append(
            'destructibles_authority.py commits before native #1513 destroy')
    copied_fell = _function_at_any_indent(
        destructibles_sensor, '_fell_trees_near') or ''
    copied_try = _function_at_any_indent(
        destructibles_sensor, '_try_destroy_destructible') or ''
    copied_solid = _function_at_any_indent(
        destructibles_sensor, '_try_destroy_solid_hit') or ''
    for required in ('def set_catalog', 'def _world_catalog_boxes',
                     'def _catalog_intersections',
                     'def _segment_world_box_interval',
                     'def _instance_descriptor_filename_1513',
                     'def _catalog_shot_intersection',
                     'def _vehicle_swept_box',
                     'def _vehicle_contact_box',
                     'def _catalog_motion_blocked',
                     '_SOFT_STATIC_MAX_SKIPS = 4',
                     'def note_destroyed',
                     '_NATIVE_HIDE_MIN_SECONDS = 0.2',
                     'max(_NATIVE_HIDE_MIN_SECONDS, delay)',
                     "kind not in ('fragile', 'structure', 'falling')",
                     "g_offh_destr_pending",
                     'def set_event_sink', 'def _publish_destroyed',
                     'def _decode_mat_info_1513',
                     '(collided, hitPt, surfNormal, matKind, fname,',
                     'itemIndex, chunkID) = payload',
                     'return hitPt, surfNormal, chunkID, itemIndex, '
                     'matKind, fname',
                     "'destructible_kind'", 'shot_yaw, 12.0, True',
                     'if not _destr_ok:', 'if not _ok:',
                     '_object_pos = Math.Vector3(_tx, _ty, _tz)',
                     'def _solid_destructible_candidate_1513',
                     '_SOLID_CONTACT_RADIUS_1513 = 0.5',
                     '_SOLID_CONTACT_NORMAL_DOT_1513 = 0.5',
                     'hit_pt + _incoming.scale(3.0)',
                     'hit_pt - _incoming.scale(2.0)',
                     'modules.get(matKind)',
                     "g_offh_destr_instances",
                     'matrix.applyVector(',
                     'catalog_version >= 3',
                     'def _catalog_instance_for_matrix_1513',
                     "'descriptor_filename': _filename",
                     "'ambiguous_instances': ambiguous_signatures",
                     'def _native_chunk_destructible_count_1513',
                     "'_DestructiblesManager__loadedChunkIDs'",
                     'for _ti in xrange(_native_count):',
                     "_dfn[_ti] if _ti < len(_dfn) else ''",
                     'filename prefix exceeds native count',
                     "_raw_normalized != _located['filename']",
                     'def _falling_initial_matrix_1513',
                     'def _refresh_destroyed_falling_instances_1513',
                     '_initial_matrix, _cm_t, Math',
                     'BigWorld.wg_getDestructibleEffectCategory(',
                     'native destructible destroy was not accepted'):
        if required not in destructibles_sensor:
            errors.append(
                'destructibles_sensor.py omits strict #1513 seam: %s' %
                required)
    catalog_motion = _function_at_any_indent(
        destructibles_sensor, '_catalog_motion_blocked') or ''
    for required in (
            '_vehicle_swept_box(', '_catalog_contact_candidates(',
            '_vehicle_contact_box(',
            'travel=float(vel) * max(0.0, float(dt))',
            '_refresh_destroyed_falling_instances_1513(',
            '_stock_crushable_1513(', 'auth.destroy_fragile(',
            'auth.destroy_module(', 'auth.destroy_column(',
            'kinetic_speed is not None and not contact_candidate',
            'if kinetic_commit:',
            'commit_candidates.append((candidate, kinetic_speed, True))',
            'used_kinetic_speed = used_kinetic_speed or used_cap',
            "kind == 'falling' and",
            "auth.is_destroyed(chunk_id, item_index, None)",
            'float(now) < float(deadline)', 'note_destroyed(',
            '_publish_destroyed('):
        if required not in catalog_motion:
            errors.append('destructibles_sensor.py catalog motion omits %s' %
                          required)
    if catalog_motion.find('float(now) < float(deadline)') > \
            catalog_motion.find('_stock_crushable_1513('):
        errors.append('destructibles_sensor.py destroys before enforcing the '
                      'native hiding interval')
    contact_box = _function_at_any_indent(
        destructibles_sensor, '_vehicle_contact_box') or ''
    for required in (
            'epsilon=0.075',
            'minimum_forward = float(minimum[2]) - margin + travel',
            'maximum_forward = float(maximum[2]) + margin + travel'):
        if required not in contact_box:
            errors.append('destructibles_sensor.py low-speed contact box omits '
                          '%s' % required)
    soft_static = _function_at_any_indent(
        destructibles_sensor, '_catalog_soft_static_path') or ''
    for required in (
            'for candidate_index in range(_SOFT_STATIC_MAX_SKIPS):',
            '_registered_shot_exit_1513(',
            'allow_kinetic_first and kinetic_speed is not None',
            "return 'pending_hard' if pending_contact else False"):
        if required not in soft_static:
            errors.append('destructibles_sensor.py soft-chain boundary omits '
                          '%s' % required)
    shot_world = _function_at_any_indent(
        destructibles_sensor, 'shot_world_distance') or ''
    for required in (
            'bigworld.wg_getMatInfoNearPoint(',
            '_catalog_candidate_for_native_identity_1513(',
            '_catalog_candidate_at_contact(',
            '_catalog_shot_intersection(',
            'world_dist if world_collision is not None else None',
            "catalog_hit['ambiguous']",
            "catalog_hit['exit_distance']",
            '_registered_shot_exit_1513(',
            '_scaled_shot_through_health_1513(',
            'health <= _SHOT_THROUGH_MAX_HP_1513',
            'piercing_loss=_SHOT_THROUGH_MIN_REDUCTION_1513',
            'loss_distance=world_dist',
            "loss_distance=catalog_hit['distance']",
            'stopped_by_destructible=True',
            '_try_destroy_destructible(',
            'shot_yaw, 12.0, True'):
        if required not in shot_world:
            errors.append('destructibles_sensor.py shot fallback omits %s' %
                          required)
    for required in (
            '_SHOT_THROUGH_MAX_HP_1513 = 19.0',
            '_SHOT_THROUGH_MIN_REDUCTION_1513 = 25.0',
            "'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR'",
            'def _scaled_shot_through_health_1513('):
        if required not in destructibles_sensor:
            errors.append('destructibles_sensor.py shooting-through contract '
                          'omits %s' % required)
    resolve_scene = _function_at_any_indent(
        battle_runtime, '_resolve_shot_scene') or ''
    for required in (
            'penetration_factor=None',
            'added_loss = max(0.0',
            'piercing_loss += added_loss',
            'if added_loss > 0.0 and penetration_factor is None:',
            'combat_rules.sample_penetration_factor())',
            "loss_distance = result.get('loss_distance')",
            "loss_distance = result.get('continue_from')",
            'combat_rules.sampled_piercing(',
            'penetration_factor,',
            'piercing_loss) < 1.0',
            "'penetration_factor': penetration_factor",
            "result.get('stopped_by_destructible')",
            "raise RuntimeError('#1513 destructible shot traversal exceeded 64 hits')"):
        if required not in resolve_scene:
            errors.append('battle_runtime.py ordered destructible traversal '
                          'omits %s' % required)
    for function_name in ('_resolve_bot_shot', '_resolve_hit'):
        shot_path = _function_at_any_indent(
            battle_runtime, function_name) or ''
        for required in (
                'penetration_factor = scene.get(\'penetration_factor\')',
                'if penetration_factor is None:',
                'combat_rules.sample_penetration_factor()',
                'penetration_factor=penetration_factor'):
            if required not in shot_path:
                errors.append('battle_runtime.py %s lazy penetration '
                              'contract omits %s' %
                              (function_name, required))
    combat_rules = _text(os.path.join(package, 'combat_rules.py'))
    for required in (
            'def sample_penetration_factor(',
            'def range_piercing(',
            'def sampled_piercing(',
            'range_piercing(shot, distance) *',
            'float(penetration_factor) - float(pierce_loss or 0.0)',
            'pierce *= float(penetration_factor)',
            'pierce -= float(pierce_loss or 0.0)'):
        if required not in combat_rules:
            errors.append('combat_rules.py ordered penetration omits %s' %
                          required)
    refresh_falling = _function_at_any_indent(
        destructibles_sensor,
        '_refresh_destroyed_falling_instances_1513') or ''
    for required in (
            '_falling_native_state_1513(',
            'BigWorld.wg_getDestructibleMatrix(',
            "instance['boxes'] = boxes",
            '_index_catalog_instance_1513(',
            'initial_matrix, synthetic_collision_active =',
            'if synthetic_collision_active:',
            "instance['bin_keys'] = ()",
            'del active[identity]'):
        if required not in refresh_falling:
            errors.append(
                'destructibles_sensor.py falling refresh omits %s' % required)
    falling_native_state = _function_at_any_indent(
        destructibles_sensor, '_falling_native_state_1513') or ''
    if "'touchdownCallback' in matches[0]" not in falling_native_state:
        errors.append('destructibles_sensor.py does not bind synthetic falling '
                      'collision lifetime to native touchdown')
    note_falling = _function_at_any_indent(
        destructibles_sensor, 'note_destroyed') or ''
    if ("kind not in ('fragile', 'module', 'column')" not in note_falling or
            "if kind == 'column':" not in note_falling or
            "active[identity] = {'last_refresh': None}" not in note_falling):
        errors.append('destructibles_sensor.py routes falling atoms through '
                      'the fragile 0.2-second pending boundary')
    if ('LOG_DEBUG(\'Destr Exception:' in copied_try or
            'except Exception as e:' in copied_try or
            'except Exception:\n\t\tpass' in copied_solid or
            copied_fell.rfind("_st['felled'].add(_key)") <
            copied_fell.rfind('_publish_destroyed(')):
        errors.append(
            'destructibles_sensor.py retains a silent or premature success')
    for forbidden in (
            'hitPt, surfNormal, chunkID, itemIndex, matKind, fname = matInfo',
            'if _mi is not None:',
            'mat_info is not None and'):
        if forbidden in destructibles_sensor:
            errors.append(
                'destructibles_sensor.py retains the 0.8.2 material-hit ABI: '
                '%s' % forbidden)
    for required in (
            'FORMAT_VERSION = 3',
            "instances = data.get('instances')",
            "ambiguous_instances = data.get('ambiguous_instances')",
            'len(row) != 14', 'len(row) != 13', 'len(row[12]) < 2',
            "int(census.get('instance_signatures')) == len(instances)",
            "int(census.get('ambiguous_instance_signatures')) ==",
            "int(census.get('ambiguous_instance_candidates')) =="):
        if required not in prebaked_destructibles:
            errors.append(
                'prebaked_destructibles.py omits schema-v3 boundary: %s' %
                required)
    world_collision = _text(os.path.join(package, 'world_collision.py'))
    for required in (
            'def _check_horizontal_collision',
            'def _solid_contact_cleared',
            'def _destroy_and_recast',
            'def _drivable_ground_profile',
            'def _drivable_surface',
            '_drivable_ground_profile(_heights, _segment)',
            '_drivable_surface(col_bot, _gradient_limit)',
            'def _raised_ray_has_wall',
            '_MAX_DESCENDING_GRADIENT = 1.75',
            '_WORLD_SOFT_RECAST_BUDGET = 4',
            'for offset_x in (-hw, 0, hw)',
			'col_bot = BigWorld.wg_collideSegment',
			'for _height in (1.1, 1.6):',
			'if not _try_destroy_solid_hit(',
			'cleared = _solid_contact_cleared(',
			"if cleared == 'kinetic':",
			'_diagnostic_static_recast_1513(cleared)'):
        if required not in world_collision:
            errors.append('world_collision.py omits copied/version-local '
                          'wall boundary: %s' % required)
    if 'if matInfo:' in world_collision:
        errors.append(
            'world_collision.py treats the truthy #1513 miss payload as a hit')
    vehicle_physics = _text(os.path.join(package, 'vehicle_physics.py'))
    if 'COAST_BRAKE_SHARE = 0.65' not in vehicle_physics:
        errors.append('vehicle_physics.py omits the conservative 0.3.76 '
                      'neutral-coast calibration')
    derive_params = _function_at_any_indent(
        vehicle_physics, 'derive_params') or ''
    for required in ("if 'brakeForce' in tdp:",
                     'raw_brake = float(tdp[\'brakeForce\'])',
                     'if raw_brake > 0.0:',
                     'raw_brake / max(p[\'mass\'], 1.0)'):
        if required not in derive_params:
            errors.append('vehicle_physics.py zero-brakeForce fallback omits '
                          '%s' % required)
    longitudinal_step = _function_at_any_indent(
        vehicle_physics, 'longitudinal_step') or ''
    for required in (
            'downhill_tangent = max(',
            'math.tan(slope_pitch) * motion_sign',
            'downhill_tangent / SLIDE_HOLD_TAN',
            'coast_share = COAST_BRAKE_SHARE * (1.0 - downhill_relief)',
            'resist = rr + coast_share * grip'):
        if required not in longitudinal_step:
            errors.append('vehicle_physics.py downhill-neutral coast omits %s' %
                          required)
    strict_descriptor_contracts = (
        (_text(os.path.join(package, 'internal_geometry.py')),
         ('if isinstance(value, dict)',
          'return getattr(value, key, default)'),
         ('return value[key]',),
         'internal_geometry.py'),
        (_text(os.path.join(package, 'internal_hit_layouts.py')),
         ('if isinstance(value, dict)',
          'return getattr(value, key, default)'),
         ('return value[key]',),
         'internal_hit_layouts.py'),
        (destructibles_sensor,
         ('def _descriptor_value', 'if isinstance(value, dict)',
          'return getattr(value, name, default)',
          'def _vehicle_hull_bbox'),
         ("'hitTester' in td.hull", "td.hull['hitTester']"),
         'destructibles_sensor.py'),
        (world_collision,
         ('_vehicle_hull_bbox(td)',),
         ("'hitTester' in td.hull", "td.hull['hitTester']"),
         'world_collision.py'),
        (_text(os.path.join(package, 'tank_collision.py')),
         ('if isinstance(container, dict)',
          'return getattr(container, name, default)'),
         ('return container.get(name, default)\n\texcept AttributeError',),
        'tank_collision.py'),
        (gun_mechanics,
         ('chassis_factors = _field(',
          "descriptor.chassis, 'shotDispersionFactors')"),
         ("descriptor.chassis['shotDispersionFactors']",),
         'gun_mechanics.py'),
        (vehicle_physics,
         ("isinstance(ch, dict)",
          "getattr(ch, 'rotationSpeed', None)"),
         ("'rotationSpeed' in ch", "ch['rotationSpeed']"),
         'vehicle_physics.py'),
        (critical_damage,
         ("_descriptor_value(td.chassis, 'hullPosition')",
          "_descriptor_value(td.hull, 'turretPositions')[0]",
          "_descriptor_value(td.hull, 'hitTester').bbox"),
         ("td.chassis['hullPosition']", "td.hull['turretPositions']",
          "td.hull['hitTester']"),
         'critical_damage.py'),
    )
    for source, required_items, forbidden_items, source_name in \
            strict_descriptor_contracts:
        for required in required_items:
            if required not in source:
                errors.append('%s omits strict #1513 attribute access: %s' %
                              (source_name, required))
        for forbidden in forbidden_items:
            if forbidden in source:
                errors.append('%s uses forbidden #1513 mapping access: %s' %
                              (source_name, forbidden))
    tank_collision = _text(os.path.join(package, 'tank_collision.py'))
    if 'def forget_chassis_shape(type_descriptor)' not in tank_collision:
        errors.append('tank_collision.py omits descriptor-scoped shape '
                      'cache cleanup')
    if re.search(
            r'if\s+type_descriptor\s+is\s+None\s*:\s*return\s+DEFAULT_SHAPE',
            tank_collision):
        errors.append('tank_collision.py silently substitutes a default body '
                      'for a missing #1513 descriptor')
    support_rise = _function_at_any_indent(
        tank_collision, 'support_rise_is_obstacle') or ''
    for required in (
            'maximum_step=0.85',
            'rise = float(support_y) - float(body_y)',
            'limit = min(max(0.0, float(maximum_climb))',
            'limit += max(0.0, float(slop))',
            'return rise > limit'):
        if required not in support_rise:
            errors.append('tank_collision.py support-rise gate omits %s' %
                          required)
    local_vertical_motion = _function_at_any_indent(
        battle_runtime, '_update_vertical_motion') or ''
    for required in (
            'tank_collision.support_rise_is_obstacle(',
            "getattr(self, '_local_support_tick_pose', None)",
            'position = tuple(tick_pose)',
            'self._local_support_rise_blocked = True'):
        if required not in local_vertical_motion:
            errors.append('battle_runtime.py support-rise rollback omits %s' %
                          required)
    first_local_placement = local_vertical_motion.find(
        'if not self._local_fall_armed:')
    local_support_reject = local_vertical_motion.find(
        'elif tank_collision.support_rise_is_obstacle(')
    if (first_local_placement < 0 or local_support_reject < 0 or
            first_local_placement > local_support_reject):
        errors.append('battle_runtime.py applies support-rise rejection before '
                      'first terrain placement')
    for required in ('world_collision.check_horizontal_collision',
                     'shot_world_distance', "'maxDistance', 5000.0",
                     "event.get('shot_yaw')",
                     "event.get('shot_pitch')"):
        if required not in battle_runtime:
            errors.append('battle_runtime.py does not reuse %s' % required)
    motion_clear = _function_at_any_indent(
        battle_runtime, '_motion_is_clear') or ''
    static_index = motion_clear.find(
        'world_collision.check_horizontal_collision(')
    dynamic_index = motion_clear.find('_catalog_motion_blocked(')
    if static_index < 0 or dynamic_index < 0 or static_index > dynamic_index:
        errors.append('battle_runtime.py does not keep static world collision '
                      'authoritative before dynamic catalog contact')
    for required in (
            'kinetic_commit=bool(kinetic_speed is not None)',
            "detail.get('used_kinetic_speed', False)",
            'self._local_motion_cap_crushed = True',
            'accepted_now and status == \'crushed\''):
        if required not in motion_clear:
            errors.append('battle_runtime.py local cap-crush seam omits %s' %
                          required)
    local_drive = _function_at_any_indent(
        battle_runtime, '_drive_local') or ''
    for required in (
            'previous_speed = self._local_speed',
            'throttle * self._local_speed > 0.0',
            'if self._local_motion_cap_crushed:',
            'self._local_speed = previous_speed'):
        if required not in local_drive:
            errors.append('battle_runtime.py local cap hold omits %s' % required)
    for required in (
            'support_blocked = self._local_support_rise_blocked',
            'self._local_speed *= 0.35 ** (dt * 60.0)',
            'self._local_grind = 4'):
        if required not in local_drive:
            errors.append('battle_runtime.py local support-rise wall response '
                          'omits %s' % required)
    bot_motion = _function_at_any_indent(
        battle_runtime, '_resolve_bot_motion') or ''
    for required in (
            'movement_dir * float(speed) > 0.0',
            'rotation_dir == 0',
            'abs(turn_speed) <= 0.01',
            "'motion_world_receipt_reusable'",
            'callable(receipt_reusable)',
            'travel_yaw = (float(yaw) if speed >= 0.0 else',
            'receipt_reusable(',
            'bot_id, position, travel_yaw, speed, now, dt)',
            '_catalog_hull_contact(',
            'world_collision.check_horizontal_collision(',
            'kinetic_commit=allow_crush_drive',
            "return 'cap_crushed'"):
        if required not in bot_motion:
            errors.append('battle_runtime.py Bot contact seam omits %s' %
                          required)
    bot_guard_index = bot_motion.find('_catalog_hull_contact(')
    bot_receipt_index = bot_motion.find('receipt_reusable(\n')
    bot_world_index = bot_motion.find(
        'world_collision.check_horizontal_collision(')
    bot_catalog_index = bot_motion.find('_catalog_motion_blocked(')
    if (bot_receipt_index < 0 or bot_guard_index < 0 or bot_world_index < 0 or
            bot_catalog_index < 0 or not (
                bot_receipt_index < bot_guard_index <
                bot_world_index < bot_catalog_index)):
        errors.append('battle_runtime.py does not require typed receipt plus '
                      'catalog guard before world-first exact Bot commit')
    direction_probe = _function_at_any_indent(
        battle_runtime, '_direction_probe') or ''
    for required in (
            '20.0 if abs(float(speed or 0.0)) > 5.0 else 15.0',
            '_catalog_soft_static_path(',
            'allow_kinetic_first=True',
            "if soft_status == 'kinetic':"):
        if required not in direction_probe:
            errors.append('battle_runtime.py Bot corridor omits %s' % required)
    for forbidden in ('_direction_world_receipt(', "result['world_receipt']"):
        if forbidden in direction_probe:
            errors.append('battle_runtime.py charges final-motion receipt rays '
                          'to generic planner alternatives: %s' % forbidden)
    direction_receipt = _function_at_any_indent(
        battle_runtime, '_direction_world_receipt') or ''
    for required in (
            'proof_distance = 15.0',
            'for offset in (-half_width, 0.0, half_width):',
            'for height in (0.6, 1.1, 1.6):',
            '_catalog_soft_static_path(',
            "if soft_status in (True, 'kinetic'):",
            "if soft_status == 'deferred':",
            "'distance': proof_distance",
            "'half_width': half_width",
            "'leading': leading",
            "'origin': (x, y, z)",
            "'yaw': float(travel_yaw)",
            "'direction': (-1 if signed_speed < 0.0 else 1)"):
        if required not in direction_receipt:
            errors.append('battle_runtime.py typed world receipt omits %s' %
                          required)
    world_receipt_contains = _function_at_any_indent(
        bot_runtime, '_world_receipt_contains') or ''
    for required in (
            "origin = receipt.get('origin')",
            "receipt_yaw = _number(receipt.get('yaw'))",
            "receipt_sign = int(_number(receipt.get('direction')))",
            'receipt_sign != current_sign',
            'frame_step = max(0.0, min(0.2, _number(dt)))',
            'abs(_number(speed)) * frame_step + 0.2',
            'receipt_forward >= -0.0001',
            'receipt_forward + leading + current_reach <= distance',
            'rdy <= 0.0001', 'receipt_lateral <= 0.0001',
            'receipt_angle <= 0.00001'):
        if required not in world_receipt_contains:
            errors.append('bot_runtime.py typed world receipt reuse omits %s' %
                          required)
    contained_receipt = _function_at_any_indent(
        bot_runtime, '_contained_cached_world_receipt') or ''
    for required in (
            "result.get('deferred', False)",
            'not BotRuntime._probe_is_clear(result)',
            "receipt = result.get('world_receipt')",
            'BotRuntime._world_receipt_contains(',
            'receipt, position, travel_yaw, speed, dt)',
            'return receipt'):
        if required not in contained_receipt:
            errors.append('bot_runtime.py independent receipt carry omits %s' %
                          required)
    motion_probe_reusable = _function_at_any_indent(
        bot_runtime, '_motion_probe_reusable') or ''
    for required in (
            "cached['result'].get(", "'deferred', False",
            'now >= cached.get(\'deadline\', 0.0)',
            "receipt = (cached.get('result') or {}).get('world_receipt')",
            'BotRuntime._world_receipt_contains(',
            'receipt, position, travel_yaw, speed, dt'):
        if required not in motion_probe_reusable:
            errors.append('bot_runtime.py planning-cache receipt guard omits %s' %
                          required)
    receipt_reusable = _function_at_any_indent(
        bot_runtime, 'motion_world_receipt_reusable') or ''
    for required in (
            'self._motion_probe_cache.get(int(bot_id))',
            "result.get('world_receipt')",
            'return self._motion_probe_reusable(',
            'cached, position, travel_yaw, speed, now, False, dt'):
        if required not in receipt_reusable:
            errors.append('bot_runtime.py typed world receipt adapter omits %s' %
                          required)
    receipt_frame_begin = _function_at_any_indent(
        bot_runtime, '_begin_world_receipt_frame') or ''
    for required in (
            'for entry in self._world_receipt_waiting:',
            'state = self.states.get(bot_id)',
            "state.get('alive', True)",
            'initial_waiting = [',
            'bot_id for bot_id, initial in waiting if initial]',
            'priority_source = (initial_waiting if initial_waiting else',
            'self._world_receipt_budget = MAX_WORLD_RECEIPTS_PER_FRAME',
            "'waiting_initial': dict(waiting)",
            "'initial_first': bool(initial_waiting)",
            "'requested': []", "'requested_set': set()",
            "'request_uncached': {}", "'attempted': set()",
            "'attempt_deferred': []"):
        if required not in receipt_frame_begin:
            errors.append('bot_runtime.py receipt-frame admission omits %s' %
                          required)
    receipt_frame_finish = _function_at_any_indent(
        bot_runtime, '_finish_world_receipt_frame') or ''
    for required in (
            "requested = frame['requested_set']",
            "attempted = frame['attempted']",
            "for bot_id, unused_previous_uncached in frame['waiting']:",
            'if bot_id in requested and bot_id not in attempted:',
            'append_once(bot_id, request_uncached.get(bot_id, False))',
            "for bot_id in frame['requested']:",
            "deferred = set(frame['attempt_deferred'])",
            'if bot_id in deferred:',
            'append_once(bot_id, False)',
            'self._world_receipt_waiting = next_waiting'):
        if required not in receipt_frame_finish:
            errors.append('bot_runtime.py eligible receipt rotation omits %s' %
                          required)
    if "for bot_id in frame['attempt_deferred']:" in receipt_frame_finish:
        errors.append('bot_runtime.py reorders callback-deferred receipts by '
                      'fixed Bot encounter order instead of the waiting FIFO')
    waiting_loop = (
        "for bot_id, unused_previous_uncached in frame['waiting']:")
    requested_loop = "for bot_id in frame['requested']:"
    waiting_unserved = receipt_frame_finish.find(waiting_loop)
    requested_unserved = receipt_frame_finish.find(
        requested_loop, waiting_unserved + 1)
    deferred_set = receipt_frame_finish.find(
        "deferred = set(frame['attempt_deferred'])", requested_unserved + 1)
    waiting_deferred = receipt_frame_finish.find(
        waiting_loop, deferred_set + 1)
    requested_deferred = receipt_frame_finish.find(
        requested_loop, waiting_deferred + 1)
    waiting_commit = receipt_frame_finish.find(
        'self._world_receipt_waiting = next_waiting', requested_deferred + 1)
    if not (0 <= waiting_unserved < requested_unserved < deferred_set <
            waiting_deferred < requested_deferred < waiting_commit):
        errors.append('bot_runtime.py receipt FIFO does not preserve unserved '
                      'work before rotating native-deferred attempts')
    world_receipt_probe = _function_at_any_indent(
        bot_runtime, '_probe_world_receipt') or ''
    for required in (
            'if not callable(self.world_receipt_probe):',
            "if bot_id not in frame['requested_set']:",
            "frame['requested'].append(bot_id)",
            "frame['requested_set'].add(bot_id)",
            "waiting_initial = frame['waiting_initial']",
            'waiting_initial[bot_id] if bot_id in waiting_initial else',
            'bool(uncached)',
            "if (frame['initial_first'] and",
            "not frame['request_uncached'][bot_id]):",
            'if priority and bot_id not in priority:',
            'priority.discard(bot_id)',
            'if self._world_receipt_budget <= 0:',
            "return 'deferred'",
            'self._world_receipt_budget -= 1',
            "frame['attempted'].add(bot_id)",
            'result = self.world_receipt_probe(',
            "if result == 'deferred':",
            "frame['attempt_deferred'].append(bot_id)",
            'except Exception:',
            'return None'):
        if required not in world_receipt_probe:
            errors.append('bot_runtime.py final-motion receipt probe omits %s' %
                          required)
    bot_update = _function_at_any_indent(bot_runtime, 'update') or ''
    for required in (
            'self._begin_world_receipt_frame()',
            'motion_probe = sample_direction(travel_yaw)',
            'not motion_probe.get(\'deferred\', False)',
            "abs(_number(motion_probe.get('slope'))) <= 0.01",
            'abs(throttle) > 0.01 and abs(turn) <= 0.01',
            'not state.get(\'airborne\', False)',
            'receipt = self._contained_cached_world_receipt(',
            'cached_motion_probe, position, travel_yaw,',
            'receipt_speed, step)',
            'if receipt is not None:',
            "motion_probe['world_receipt'] = receipt",
            'else:',
            'receipt = self._probe_world_receipt(',
            "}).get('world_receipt'), dict))",
            "if receipt == 'deferred':",
            "motion_probe['deferred'] = True",
            "motion_probe['clear'] = False",
            "motion_probe['collision'] = False",
            'elif receipt is False:',
            "'clear': False", "'collision': True",
            "motion_probe['world_receipt'] = receipt",
            "not motion_probe.get('deferred', False)",
            "self._motion_probe_cache.pop(state['id'], None)",
            'probe_deferred = bool(',
            'if callable(remember) and not probe_deferred:',
            'if probe_deferred:',
            "elif motion_status == 'cap_crushed':",
            'speed = previous_speed',
            "state.pop('destructible_contact_speed', None)",
            "state['speed'] = speed",
            'self._finish_world_receipt_frame()'):
        if required not in bot_update:
            errors.append('bot_runtime.py cap-crush publication seam omits %s' %
                          required)
    for required in (
            'now + SHOT_LANE_REFRESH_SECONDS + 1e-9 >=',
            'self._next_observation, shot_lane_budget,',
            'if collect_observation and not shot_lanes_ready:',
            "publish and command['fire_allowed'] and target is not None",
            'self._shot_clear(',
            'self._next_observation = _number(now) + OBSERVATION_SECONDS'):
        if required not in bot_update:
            errors.append('bot_runtime.py 0.40/0.20 observation and final-lane '
                          'schedule omits %s' % required)
    if bot_runtime.count('self._invalidate_realised_motion(') != 3:
        errors.append('bot_runtime.py must invalidate exactly the support-rise, '
                      'realised-pose and hard-contact rollback paths')
    if 'world_receipt_backlog = set(' in bot_update:
        errors.append('bot_runtime.py rebuilds a static alive/no-receipt '
                      'backlog instead of rotating actual eligible requests')
    sample_index = bot_update.find('motion_probe = sample_direction(travel_yaw)')
    receipt_index = bot_update.find('receipt = self._probe_world_receipt(')
    cache_index = bot_update.find(
        "self._motion_probe_cache[state['id']] = {")
    if (sample_index < 0 or receipt_index < 0 or cache_index < 0 or
            not (sample_index < receipt_index < cache_index)):
        errors.append('bot_runtime.py does not limit typed world receipt to the '
                      'final selected motion sample before cache admission')
    begin_index = bot_update.find('self._begin_world_receipt_frame()')
    finish_index = bot_update.find('self._finish_world_receipt_frame()')
    if (begin_index < 0 or finish_index < 0 or
            not (begin_index < sample_index < finish_index)):
        errors.append('bot_runtime.py does not bracket eligible receipt requests '
                      'with one render-frame scheduler')
    for required in ('world_receipt_probe=self._direction_world_receipt',):
        if required not in battle_runtime:
            errors.append('battle_runtime.py omits Bot final-motion receipt '
                          'injection: %s' % required)
    for required in (
            'MAX_WORLD_RECEIPTS_PER_FRAME = 13',
            'def _motion_probe_deadline(now, entity_id, initial=False):',
            'if not initial:',
            'return float(now) + MOTION_PROBE_SECONDS',
            "phase = (((abs(int(entity_id)) * 17 + 7 * 11) % 29) + 1) / 29.0",
            'return float(now) + MOTION_PROBE_SECONDS * phase'):
        if required not in bot_runtime:
            errors.append('bot_runtime.py receipt scheduler omits %s' % required)
    if bot_update.find("motion_probe['deferred'] = True") > \
            bot_update.find("self._motion_probe_cache[state['id']] = {"):
        errors.append('bot_runtime.py caches receipt work before deferral is '
                      'classified')
    lan_client = _text(os.path.join(package, 'lan_client.py'))
    for required in ("'battle_ready'", "'battle_live'", 'reported_health',
                     'message[\'_client_received_time\']',
                     'def _monotonic_time', 'self._combat_timing_tick',
                     'def _load_server_timing',
                     'def _attach_critical_proposal',
                     "'critical_target_base_revision'",
                     "'critical_target_ack_seq'", "'hull_damage'",
                     "elif kind == 'bot_observation':",
                     "'invalid bot observation message'"):
        if required not in lan_client:
            errors.append('lan_client.py omits protocol boundary: %s' %
                          required)
    bot_state_fields_match = re.search(
        r'_BOT_STATE_WIRE_FIELDS\s*=\s*\((.*?)\)\s*STATE_BARRIER_TYPES',
        lan_client, re.S)
    if bot_state_fields_match is None:
        errors.append('lan_client.py omits finite Bot-state wire allowlist')
    else:
        bot_state_fields = bot_state_fields_match.group(1)
        for required in (
                "'id'", "'x'", "'y'", "'z'", "'yaw'", "'aim_yaw'",
                "'gun_pitch'", "'movement_dir'", "'rotation_dir'",
                "'fire_seq'", "'shell_index'", "'next_shell_index'",
                "'ammo_remaining'", "'ammo_reload_pending'",
                "'health'", "'alive'",
                "'critical'", "'combat_base_revision'", "'combat_seq'",
                "'combat_fire_elapsed'", "'combat_fire_timer'",
                "'death_reason'", "'display_health'", "'world_pose'"):
            if required not in bot_state_fields:
                errors.append('lan_client.py Bot-state wire allowlist omits %s' %
                              required)
        for forbidden in (
                "'profile'", "'route'", "'physics'", "'collision'",
                "'world_receipt'", "'shot_origin'", "'shot_velocity'",
                "'shot_gravity'", "'shot_proof'"):
            if forbidden in bot_state_fields:
                errors.append('lan_client.py Bot-state wire allowlist leaks %s' %
                              forbidden)
    project_bot_state = _function_at_any_indent(
        lan_client, '_project_bot_state') or ''
    for required in (
            'for name in _BOT_STATE_WIRE_FIELDS',
            'if name in state',
            "has_shot_yaw = 'shot_yaw' in state",
            "has_shot_pitch = 'shot_pitch' in state",
            'if has_shot_yaw != has_shot_pitch:',
            "projected['shot_yaw'] = state['shot_yaw']",
            "projected['shot_pitch'] = state['shot_pitch']",
            "ammo_fields = ('shell_index', 'next_shell_index', 'ammo_remaining',",
            "'ammo_reload_pending')",
            'if any(present) and not all(present):',
            "not isinstance(state.get('ammo_reload_pending'), bool)"):
        if required not in project_bot_state:
            errors.append('lan_client.py Bot-state projection omits %s' %
                          required)
    send_bot_state = _function_at_any_indent(
        lan_client, 'send_bot_state') or ''
    projection_index = send_bot_state.find('state = _project_bot_state(state)')
    send_index = send_bot_state.find("return self._send({'type': 'bot_state'")
    if (projection_index < 0 or send_index < 0 or
            projection_index > send_index):
        errors.append('lan_client.py does not project Bot state before freezing')
    battle_frame = _function_at_any_indent(battle_runtime, '_frame') or ''
    send_message_index = battle_frame.find('self._send_bot_message(outgoing)')
    resolve_fire_index = battle_frame.find('self._resolve_bot_fire(outgoing)')
    if (send_message_index < 0 or resolve_fire_index < 0 or
            send_message_index > resolve_fire_index):
        errors.append('battle_runtime.py does not retain full local Bot state '
                      'through same-frame projectile resolution')
    lan_session = _text(os.path.join(package, 'lan_session.py'))
    for required in ("elif kind == 'bot_observation':",
                     'self._battle_runtime.on_bot_observation(message)'):
        if required not in lan_session:
            errors.append('lan_session.py omits observation relay boundary: '
                          '%s' % required)
    worker = _function_at_any_indent(lan_client, '_worker') or ''
    hello_send = worker.find('sock.sendall(payload)')
    connected_publish = worker.find(
        'self._publish_connected_transport(sock, generation)')
    if (hello_send < 0 or connected_publish < 0 or
            hello_send > connected_publish):
        errors.append('lan_client.py exposes connected before hello is sent')
    publish_transport = _function_at_any_indent(
        lan_client, '_publish_connected_transport') or ''
    for required in (
            'self.connected = True',
            'self._outbound_accepting = True',
            'self._sender_thread = sender',
            'sender.start()'):
        if required not in publish_transport:
            errors.append('lan_client.py hello-complete publication omits %s' %
                          required)
    outbound_send = _function_at_any_indent(lan_client, '_send') or ''
    for required in (
            '_freeze_outbound(message, [0])',
            'len(self._outbound_queue) >= MAX_OUTBOUND_MESSAGES',
            'self._outbound_bytes + estimated_size >',
            'self._outbound_seq += 1',
            'self._outbound_queue.append((',
            "self._abort_outbound('LAN outbound queue exceeded limit'",
            'self._outbound_event.set()'):
        if required not in outbound_send:
            errors.append('lan_client.py reliable FIFO enqueue omits %s' %
                          required)
    if 'sendall(' in outbound_send or 'json.dumps(' in outbound_send:
        errors.append('lan_client.py performs wire encoding or I/O on the '
                      'caller thread')
    sender_worker = _function_at_any_indent(
        lan_client, '_sender_worker') or ''
    for required in (
            'generation == self._transport_generation',
            'item = self._dequeue_outbound(generation)',
            'send_result = self._send_wire(item[1], sock, generation)',
            'self._abort_outbound('):
        if required not in sender_worker:
            errors.append('lan_client.py sender worker omits %s' % required)
    freeze_outbound = _function_at_any_indent(
        lan_client, '_freeze_outbound') or ''
    for required in (
            'MAX_OUTBOUND_DEPTH', 'MAX_OUTBOUND_NODES',
            'math.isnan(value)', 'math.isinf(value)',
            'outbound mapping key must be text',
            'outbound payload contains non-plain data'):
        if required not in freeze_outbound:
            errors.append('lan_client.py immutable outbound snapshot omits %s' %
                          required)
    for required in (
            'MAX_OUTBOUND_MESSAGES = 256',
            'MAX_OUTBOUND_BYTES = MAX_MESSAGE_BYTES * 4',
            'LEAVE_SEND_TIMEOUT = 0.05',
            'self._transport_generation += 1',
            'def _publish_connected_transport',
            'def _record_transport_error',
            'def _abort_outbound'):
        if required not in lan_client:
            errors.append('lan_client.py bounded generation-isolated transport '
                          'omits %s' % required)
    battle_ready = _function_at_any_indent(
        lan_client, 'send_battle_ready') or ''
    for required in (
            'for team in (1, 2):',
            'points = bases.get(str(team))',
            'points = bases.get(team)',
            "wire_bases[str(team)] = points",
            "message['bases'] = wire_bases"):
        if required not in battle_ready:
            errors.append('lan_client.py battle-ready wire canonicalization '
                          'omits %s' % required)

    driver = _text(os.path.join(package, 'ai', 'driver.py'))
    remember_failure = _function_at_any_indent(
        driver, 'remember_failure') or ''
    for required in (
            "state['failed_yaws'][self._yaw_key(yaw)]",
            "state.get('last_desired_yaw')",
            "state['escape_side'] = side",
            "state['escape_side_until'] = (",
            "state['steering_yaw'] = None",
            "state['plan_age'] = 999.0"):
        if required not in remember_failure:
            errors.append('ai/driver.py stable failure recovery omits %s' %
                          required)
    choose_yaw = _function_at_any_indent(driver, '_choose_yaw') or ''
    for required in (
            "state.get('escape_side_until', 0.0) > state['clock']",
            "state.get('escape_side', 0.0)",
            'score += 1.25'):
        if required not in choose_yaw:
            errors.append('ai/driver.py stable escape-side steering omits %s' %
                          required)
    drive = _function_at_any_indent(driver, 'drive') or ''
    for required in (
            "state['last_desired_yaw'] = desired_yaw",
            'climb_grade > 0.10 and abs(delta) > 0.30 and not avoiding',
            'throttle = 0.0'):
        if required not in drive:
            errors.append('ai/driver.py uphill alignment omits %s' % required)
    for required in (
            'WAYPOINT_ARRIVAL_RADIUS = 1.5',
            'TRAFFIC_WAIT_LEASE_SECONDS = 1.5'):
        if required not in driver:
            errors.append('ai/driver.py traffic/arrival contract omits %s' %
                          required)
    traffic_wait = _function_at_any_indent(driver, 'wait_for_traffic') or ''
    for required in (
            "state['traffic_waiting'] = True",
            "state['traffic_wait_time'] += max(",
            "float(state.get('last_step', 0.0))",
            "state['traffic_wait_time'] <= TRAFFIC_WAIT_LEASE_SECONDS",
            "state['stuck_time'] = 0.0",
            "state['recovery_time'] = 0.0"):
        if required not in traffic_wait:
            errors.append('ai/driver.py finite traffic wait omits %s' % required)
    for required in (
            "if not state.pop('traffic_waiting', False):",
            "state['traffic_wait_time'] = 0.0",
            'if target_distance <= WAYPOINT_ARRIVAL_RADIUS:'):
        if required not in drive:
            errors.append('ai/driver.py traffic/arrival drive seam omits %s' %
                          required)

    navigation = _text(os.path.join(package, 'ai', 'navigation.py'))
    climb_shortcut = _function_at_any_indent(
        navigation, 'shortcut_preserves_climb_approach') or ''
    for required in (
            'minimum_grade=0.10, minimum_turn=0.30',
            'grade = (float(after[1]) - float(pivot[1])) / out_run',
            'if abs(turn) > float(minimum_turn):',
            'return False'):
        if required not in climb_shortcut:
            errors.append('ai/navigation.py climb-turn guard omits %s' %
                          required)
    live_climb_shortcut = _function_at_any_indent(
        navigation, 'live_shortcut_preserves_climb_approach') or ''
    if 'TerrainGrid.shortcut_preserves_climb_approach(' not in \
            live_climb_shortcut:
        errors.append('ai/navigation.py live climb-turn guard is disconnected')
    smooth = _function_at_any_indent(navigation, '_smooth') or ''
    if 'self.shortcut_preserves_climb_approach(' not in smooth:
        errors.append('ai/navigation.py smoothing bypasses climb-turn setup')
    next_target = _function_at_any_indent(navigation, 'next_target') or ''
    if next_target.count(
            'self.grid.live_shortcut_preserves_climb_approach(') != 3:
        errors.append('ai/navigation.py must guard reach, lookahead and '
                      'continuation shortcuts')
    for required in (
            'from gui.mods.offline_lan_0922.ai.driver import '
            'WAYPOINT_ARRIVAL_RADIUS',
            '_distance_2d(current, selected) <= WAYPOINT_ARRIVAL_RADIUS',
            '_distance_2d(current, goal) > 15.0'):
        if required not in navigation:
            errors.append('ai/navigation.py shared arrival guard omits %s' %
                          required)
    navigation_paused = _function_at_any_indent(
        navigation, 'navigation_paused') or ''
    if 'hold_radius=WAYPOINT_ARRIVAL_RADIUS' not in navigation_paused:
        errors.append('ai/navigation.py pause guard drifts from driver arrival '
                      'radius')
    server = _text(os.path.join(
        port_root, 'server', 'lan_battle_server.py'))
    for required in ('def mark_battle_ready', 'def _update_capture',
                     'def loading_snapshot', 'capture_bases',
                     'def _timing_payload', 'self.pending_live_message',
                     '"server_tick": self.tick',
                     'PREBATTLE_SECONDS = 15.0',
                     'RESULT_RESET_SECONDS = 5.0',
                     'def _drop_capture_for_vehicle',
                     'self.capture_contributors = {1: {}, 2: {}}',
                     'self.capture_cursors = {1: 0, 2: 0}',
                     'self.capture_threat_bases = {1: [], 2: []}',
                     'def _bot_defense_context',
                     'self._bot_defense_context()',
                     'self.capture_bases if self.client_build == CLIENT_BUILD_0922',
                     'def _critical_damage_transition',
                     "state['invaders'] = len(invader_keys)",
                     "state['time_left'] = (",
                     "state['stopped'] = bool(invader_keys and defenders > 0)",
                     'player.client_position', "state.get('world_pose')",
                     '("shot", shot_seq, "player", target_id)',
                     '"bot_manifest": list(self.bot_manifest)',
                     'def _record_frag', '"kind": "vehicle_statistics"',
                     'death_attacker_kind',
                     'def _critical_proposal_admission',
                     'event["critical_accepted"]',
                     '"stale_target_state"', '"hull_damage"',
                     'accepted_visibility=accepted_visibility',
                     'def broadcast_bot_observation',
                     'server.state.broadcast_bot_observation(relay)',
                     'def _set_protocol_reject',
                     'def should_log_protocol_reject',
                     'BOT STATE rejected authority=',
                     'BOT HIT rejected authority=',
                     'BOT HUMAN HIT rejected authority=',
                     'def _sanitize_bot_ammo',
                     'def _validate_bot_ammo_transition',
                     '"ammo_reload_pending": ammunition["pending"]'):
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
    reported_health = _function_at_any_indent(
        server, '_apply_reported_health') or ''
    for required in ('reported_attacker_kind', 'reported_attacker_id',
                     'self.pending_events.append(event)',
                     'self._record_frag('):
        if required not in reported_health:
            errors.append('lan_battle_server.py omits client-simulation '
                          'death-ledger boundary: %s' % required)
    for forbidden in ('event["attacker"]', 'event["attacker_bot"]'):
        if forbidden in reported_health:
            errors.append('lan_battle_server.py leaks ledger-only attribution '
                          'into client_simulation wire event: %s' % forbidden)
    sanitize_ammo = _function_at_any_indent(
        server, '_sanitize_bot_ammo') or ''
    for required in (
            'has_inventory = "ammo_remaining" in raw',
            'has_next = "next_shell_index" in raw',
            'has_pending = "ammo_reload_pending" in raw',
            'not isinstance(pending, bool)',
            'sum(parsed) > 1000',
            'total > 0 and not pending and parsed[loaded] <= 0'):
        if required not in sanitize_ammo:
            errors.append('lan_battle_server.py Bot-ammo sanitizer omits %s' %
                          required)
    validate_ammo = _function_at_any_indent(
        server, '_validate_bot_ammo_transition') or ''
    for required in (
            'if fire_delta not in (0, 1):',
            'next_shell = int(current.get("next_shell_index", loaded))',
            'previous_pending = bool(previous.get(',
            'if not pending:',
            'expected_loaded = (previous_next if previous_pending else',
            'if pending:',
            'if next_shell != previous_next:',
            'elif loaded != previous_next:',
            'if list(after) != expected:'):
        if required not in validate_ammo:
            errors.append('lan_battle_server.py Bot-ammo transition omits %s' %
                          required)
    server_planner = _text(os.path.join(
        port_root, 'server', 'server_bot_ai.py'))
    for required in ('class BotPlanner', 'def report_contacts',
                     'def report_affordances', 'def build_orders',
                     'score_candidates',
                     'bot["id"] not in contact.get(',
                     '"shootable_by_bot_ids", ())',
                     'accepted_visibility.append({',
                     '"armor": max(0.0, _number(raw.get("armor"), 0.0))',
                     'def _update_base_defense',
                     'MAX_BASE_DEFENDERS = 3',
                     'def _defense_eta',
                     'def _prioritize_base_invaders',
                     '"shootable_by_bot_ids", ())',
                     'def _team_base_axis',
                     'def _artillery_anchor',
                     'def _apply_artillery_order',
                     '"artillery_hold" if arrived else "artillery_deploy"',
                     'order["throttle_override"] = 0.0 if arrived else None',
                     'if class_tag == "SPG"',
                     'def _shell_index',
                     'remaining = ((state or {}).get("ammo_remaining")',
                     'fragile = armor <= he_penetration * 0.90',
                     'normal_penetration < armor * 1.05'):
        if required not in server_planner:
            errors.append('server_bot_ai.py omits documented macro boundary: %s' %
                          required)
    order_for = _function_at_any_indent(server_planner, '_order_for') or ''
    close_withdraw = 'elif distance < close_limit and dominant != "brawler":'
    firing_hold = ('elif distance <= min(fire_range, max(150.0, '
                   'desired_range * 1.35)):')
    if (close_withdraw not in order_for or firing_hold not in order_for or
            order_for.find(close_withdraw) > order_for.find(firing_hold)):
        errors.append('server_bot_ai.py makes close withdrawal unreachable')
    if 'scripts.client.gui.mods.offhangar' in server_planner:
        errors.append('server/server_bot_ai.py imports the 0.8.2 source tree')
    config = _text(os.path.join(package, 'config.py'))
    if "'prebattleCountdownSeconds': 15.0" not in config:
        errors.append('config.py default diverges from 0.8.2 countdown')
    if "'physics_tuning': {}" not in config:
        errors.append('config.py omits copied 0.8.2 physics tuning boundary')
    if "'he_tuning': {}" not in config:
        errors.append('config.py omits copied 0.8.2 HE tuning boundary')
    for required in (
            "ENDPOINT_FILE_NAME = 'server_endpoint.json'",
            "'host': '127.0.0.1'", "'port': 28782",
            'def save_endpoint(host, port, path=ENDPOINT_PATH):',
            "temporary_path = path + '.tmp'",
            "backup_path = path + '.bak'",
            'os.rename(temporary_path, path)',
            'os.rename(backup_path, path)',
            'config[\'host\'] = DEFAULT_CONFIG[\'host\']',
            'config[\'port\'] = DEFAULT_CONFIG[\'port\']'):
        if required not in config:
            errors.append('config.py omits 0.4.0 endpoint seam: %s' %
                          required)
    lobby_ui = _text(os.path.join(package, 'lobby_ui.py'))
    for required in (
            'def _auto_announcement_due(controller):',
            'gc_constants.BROWSER.CHINA_BROWSER_COUNT == 0',
            'def wrapped_lobby_inited(controller, event):',
            'automatic_open_due = bool(adapter._auto_due(controller))',
            'if automatic_open_due:\n                return None',
            'return adapter._original_lobby_inited(controller, event)',
            "current = self._controller_type.__dict__.get('onLobbyInited')",
            'if current is not self._lobby_inited_wrapper:'):
        if required not in lobby_ui:
            errors.append('lobby_ui.py omits scoped #1513 announcement seam: '
                          '%s' % required)
    for forbidden in ('hideWebBrowser', '.showBrowser =',
                      "setattr(controller_type, 'showBrowser'"):
        if forbidden in lobby_ui:
            errors.append('lobby_ui.py intercepts an explicit browser surface: '
                          '%s' % forbidden)
    bootstrap = _text(os.path.join(package, 'bootstrap.py'))
    wait_for_login = _function_at_any_indent(
        bootstrap, '_wait_for_login_space') or ''
    announcement_install = wait_for_login.find('_install_announcement_ui()')
    session_install = wait_for_login.find('_install_lan_session()')
    lobby_connect = wait_for_login.find('g_compatibility.connect(')
    if (announcement_install < 0 or session_install < 0 or lobby_connect < 0 or
            not announcement_install < session_install < lobby_connect):
        errors.append('bootstrap.py does not install the announcement seam '
                      'before the #1513 lobby is created')
    lan_session = _text(os.path.join(package, 'lan_session.py'))
    save_endpoint = _function_at_any_indent(
        lan_session, '_save_endpoint') or ''
    for required in (
            'port_config.save_endpoint(host, port)',
            "self._config['host'] = host",
            "self._config['port'] = port"):
        if required not in save_endpoint:
            errors.append('lan_session.py endpoint editor omits %s' % required)
    if (save_endpoint.find('port_config.save_endpoint(host, port)') >
            save_endpoint.find("self._config['host'] = host")):
        errors.append('lan_session.py mutates the live endpoint before the '
                      'user file is safely replaced')
    for required in (
            'SELECT A MAP, THEN CLICK CREATE TO START',
            'WAITING FOR %s TO START THE BATTLE',
            'NO ACTION NEEDED; THE BATTLE OPENS AUTOMATICALLY',
            'EDIT THE FIRST LINE TO CHANGE THE SERVER'):
        if required not in lan_session:
            errors.append('lan_session.py omits player-facing instruction: '
                          '%s' % required)
    if "vehicle_physics.apply_tuning(self._config.get('physics_tuning'))" not in battle_runtime:
        errors.append('battle_runtime.py omits copied 0.8.2 tuning order')
    if "combat_rules.apply_he_tuning(self._config.get('he_tuning'))" not in battle_runtime:
        errors.append('battle_runtime.py omits copied 0.8.2 HE tuning order')
    build = _text(os.path.join(
        repo_root, '0.9.22', 'build_wotmod.py'))
    if "'prebattleCountdownSeconds': 15.0" not in build:
        errors.append('build_wotmod.py release config diverges from 0.8.2 countdown')
    if "'physics_tuning': {}" not in build:
        errors.append('build_wotmod.py omits copied physics tuning config')
    if "'he_tuning': {}" not in build:
        errors.append('build_wotmod.py omits copied HE tuning config')
    for required in ("'host': '127.0.0.1'", "'port': 28782"):
        if required not in build:
            errors.append('build_wotmod.py omits fixed release endpoint: %s' %
                          required)
    for forbidden in ('os.environ.get(', 'server_endpoint.json'):
        if forbidden in build:
            errors.append('build_wotmod.py distributes a non-product endpoint '
                          'surface: %s' % forbidden)
    if errors:
        for error in errors:
            print('ERROR: %s' % error)
        return 1
    print('Battle source audit passed: %d port modules documented; '
          '%d frozen 0.8.2 modules classified; '
          '%d reviewed port/service hashes verified.' %
          (len(actual), len(source_actual),
           len(reviewed_port_contracts) + len(PINNED_PRODUCT_SHA256) +
           len(PINNED_PORT_SHA256) +
           len(PINNED_SERVER_SHA256) + len(PINNED_RELEASE_SHA256)))
    return 0


if __name__ == '__main__':
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), '..', '..')
    sys.exit(audit(base))
