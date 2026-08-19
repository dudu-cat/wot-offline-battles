"""Registered request handlers only; unknown command ids deliberately fail."""

from __future__ import print_function

import sys
import time
import traceback

_clock = getattr(time, 'perf_counter', None) or time.clock

from gui.mods.offline_lan_0922.account_rpc import commands
from gui.mods.offline_lan_0922.account_rpc import data
from gui.mods.offline_lan_0922.account_rpc import garage


class Result(object):
    def __init__(self, result_id, error='', stream=None, ext=None,
                 before_response=None):
        self.result_id = result_id
        self.error = error
        self.stream = stream
        self.ext = ext
        self.before_response = before_response


def _garage(context):
    """Return the mutable garage, promoting the immutable snapshot once."""
    state = context.get('garage')
    if state is None:
        state = garage.GarageState(context.get('selected_vehicle') or {})
        context['garage'] = state
    return state


def _fitting(context, mutate):
    """Apply one fitting mutation and push the resulting inventory diff.

    #1513 refreshes the garage from ``PlayerAccount.update``, which unpickles
    its argument and runs the normal ``_update`` event path.  Publishing the
    reshaped inventory section reuses the same builder a full sync uses, so the
    pushed diff and a later re-sync can never disagree.
    """
    started = _clock()
    state = _garage(context)
    try:
        mutate(state)
    except garage.GarageError as error:
        return Result(commands.RES_FAILURE, str(error))
    context['selected_vehicle'] = state.snapshot()
    mutated = _clock()
    store = context.get('garage_store')
    if store is not None:
        # A fitting happens at click speed, so saving on each accepted change
        # costs nothing and a hard client kill cannot lose an applied change.
        store.mark_dirty()
        store.flush(state.snapshot())
    saved = _clock()
    push = context.get('push_update')
    if not callable(push):
        _report_fitting_cost(started, mutated, saved, saved, None)
        return Result(commands.RES_SUCCESS)

    def publish():
        diff = data.inventory(state.snapshot(), validate=False)
        built = _clock()
        push(diff)
        _report_fitting_cost(started, mutated, saved, built, diff)

    return Result(commands.RES_SUCCESS, before_response=publish)


def _report_fitting_cost(started, mutated, saved, built, diff):
    """Log where a garage click spends its time, and how big the diff is."""
    items = 0
    if isinstance(diff, dict):
        for section in (diff.get('inventory') or {}).values():
            if isinstance(section, dict):
                for value in section.values():
                    items += len(value) if hasattr(value, '__len__') else 1
    finished = _clock()
    sys.stdout.write(
        '[Offline LAN 0.9.22] garage command ms mutate=%.1f save=%.1f '
        'build=%.1f publish=%.1f total=%.1f diff_items=%d\n' % (
            (mutated - started) * 1000.0, (saved - mutated) * 1000.0,
            (built - saved) * 1000.0, (finished - built) * 1000.0,
            (finished - started) * 1000.0, items))


def _equip_equipments(context, args):
    # [vehInvID, *getConsumablesIntCDs()]: three regular slots then the booster
    values = list(args[0] if args else ())
    if not values:
        return Result(commands.RES_FAILURE, 'INVALID_EQUIPMENT_REQUEST')
    return _fitting(context, lambda state: state.equip_equipments(
        values[0], values[1:]))


def _equip_shells(context, args):
    values = list(args[0] if args else ())
    if not values:
        return Result(commands.RES_FAILURE, 'INVALID_SHELL_REQUEST')
    return _fitting(context, lambda state: state.equip_shells(
        values[0], values[1:]))


def _equip_optional_device(context, args):
    # [shopRev, vehInvID, deviceCompDescr, slotIdx, isPaidRemoval]
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_DEVICE_REQUEST')
    return _fitting(context, lambda state: state.equip_optional_device(
        values[1], values[2], values[3]))


def _equip_component(context, args):
    # [shopRev, vehInvID, compactDescr, positionIndex, ...]
    values = list(args[0] if args else ())
    if len(values) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_MODULE_REQUEST')
    position = values[3] if len(values) > 3 else 0
    return _fitting(context, lambda state: state.install_component(
        values[1], values[2], position))


def _set_and_fill_layouts(context, args):
    # [shopRev, vehInvID, len(shells), *shells, eqType, len(eqs), *eqs], where
    # both blocks are flat descriptor/count pairs and eqs covers four slots.
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_LAYOUT_REQUEST')
    cursor = 2
    shell_count = int(values[cursor])
    cursor += 1
    shells_layout = None
    if shell_count:
        shells_layout = values[cursor:cursor + shell_count]
        cursor += shell_count
    if cursor >= len(values):
        return Result(commands.RES_FAILURE, 'INVALID_LAYOUT_REQUEST')
    equipment_type = int(values[cursor])
    cursor += 1
    equipments_layout = None
    if cursor < len(values):
        equipment_count = int(values[cursor])
        cursor += 1
        if equipment_count:
            equipments_layout = values[cursor:cursor + equipment_count]
    return _fitting(context, lambda state: state.set_layouts(
        values[1], shells_layout, equipment_type, equipments_layout))


def _add_tankman_skill(context, args):
    if len(args) < 2:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(context, lambda state: state.add_tankman_skill(
        args[0], args[1]))


def _drop_tankman_skills(context, args):
    if not args:
        return Result(commands.RES_FAILURE, 'INVALID_CREW_REQUEST')
    return _fitting(
        context, lambda state: state.drop_tankman_skills(args[0]))


def _buy_item(context, args):
    # _doCmdInt4: (cacheRev, intCompactDescr, count, goldForCredits)
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_PURCHASE_REQUEST')
    return _fitting(context, lambda state: state.buy_item(args[1], args[2]))


def _buy_and_equip_item(context, args):
    # [cacheRev, compDescr, vehInvID, slotIdx, isPaidRemoval, gunCompDescr]
    values = list(args[0] if args else ())
    if len(values) < 4:
        return Result(commands.RES_FAILURE, 'INVALID_PURCHASE_REQUEST')
    return _fitting(context, lambda state: state.buy_and_equip_item(
        values[2], values[1], values[3]))


def _vehicle_settings(context, args):
    # _doCmdInt3: (vehInvID, setting, isOn)
    if len(args) < 3:
        return Result(commands.RES_FAILURE, 'INVALID_SETTING_REQUEST')
    return _fitting(context, lambda state: state.change_vehicle_setting(
        args[0], args[1], args[2]))


def _sync_data(context, args):
    revision = args[0] if args else 0
    account_state = context.get('account_state')
    int_user_settings = (
        account_state.snapshot() if account_state is not None else {})
    return Result(commands.RES_SUCCESS, '', ext=data.sync_data(
        revision, context.get('selected_vehicle'), int_user_settings))


def _server_stats(context, args):
    receiver = context.get('receive_server_stats')
    if not callable(receiver):
        return Result(commands.RES_SUCCESS)

    def publish_stats():
        receiver({'clusterCCU': 0, 'regionCCU': 0})

    return Result(commands.RES_SUCCESS, before_response=publish_stats)


def _wrap_shop_item_prices(value, factory=None):
    """Convert wire mappings to #1513's dual-purpose ItemsPrices object."""
    if factory is None:
        from items import ItemsPrices
        factory = ItemsPrices
    current = value['items']['itemPrices']
    defaults = value['defaults']['items']['itemPrices']
    value['items']['itemPrices'] = factory(current)
    value['defaults']['items']['itemPrices'] = factory(defaults)
    return value


def _sync_shop(context, args):
    revision = args[0] if args else 0
    value = data.shop(revision, context.get('selected_vehicle'))
    value = _wrap_shop_item_prices(
        value, context.get('items_prices_factory'))
    return Result(commands.RES_STREAM, '', value)


def _sync_dossiers(context, args):
    revision = args[0] if args else 0
    return Result(commands.RES_STREAM, '', data.dossiers(revision))


def _set_language(context, args):
    return Result(commands.RES_STREAM, '', args[0] if args else '')


def _contain_queue_listeners(callback):
    # The engine's entity-call boundary logs a failing script and keeps the
    # server alive; Event.__call__ in #1513 re-raises after logging.
    try:
        callback(commands.QUEUE_TYPE_RANDOMS)
    except Exception:
        print('[Offline LAN 0.9.22] a queue-event listener failed:')
        traceback.print_exc()


def _enqueue_random(context, args):
    on_enqueued = context.get('on_enqueued')
    if not callable(on_enqueued):
        return Result(commands.RES_FAILURE, 'QUEUE_EVENTS_UNAVAILABLE')

    def enter_queue():
        _contain_queue_listeners(on_enqueued)

    return Result(commands.RES_SUCCESS, before_response=enter_queue)


def _dequeue_random(context, args):
    on_dequeued = context.get('on_dequeued')
    if not callable(on_dequeued):
        return Result(commands.RES_FAILURE, 'QUEUE_EVENTS_UNAVAILABLE')

    def leave_queue():
        _contain_queue_listeners(on_dequeued)

    return Result(commands.RES_SUCCESS, before_response=leave_queue)


def _add_int_user_settings(context, args):
    account_state = context.get('account_state')
    if account_state is None:
        return Result(commands.RES_FAILURE, 'ACCOUNT_STATE_UNAVAILABLE')
    try:
        account_state.add_int_settings(args[0] if args else ())
    except (IOError, OSError, TypeError, ValueError):
        return Result(commands.RES_FAILURE, 'INVALID_INT_USER_SETTINGS')
    return Result(commands.RES_SUCCESS)


def _del_int_user_settings(context, args):
    account_state = context.get('account_state')
    if account_state is None:
        return Result(commands.RES_FAILURE, 'ACCOUNT_STATE_UNAVAILABLE')
    try:
        account_state.del_int_settings(args[0] if args else ())
    except (IOError, OSError, TypeError, ValueError):
        return Result(commands.RES_FAILURE, 'INVALID_INT_USER_SETTINGS')
    return Result(commands.RES_SUCCESS)


HANDLERS = {
    commands.CMD_SYNC_DATA: _sync_data,
    commands.CMD_EQUIP: _equip_component,
    commands.CMD_EQUIP_OPTDEV: _equip_optional_device,
    commands.CMD_EQUIP_SHELLS: _equip_shells,
    commands.CMD_EQUIP_EQS: _equip_equipments,
    commands.CMD_SET_AND_FILL_LAYOUTS: _set_and_fill_layouts,
    commands.CMD_TMAN_ADD_SKILL: _add_tankman_skill,
    commands.CMD_TMAN_DROP_SKILLS: _drop_tankman_skills,
    commands.CMD_BUY_ITEM: _buy_item,
    commands.CMD_BUY_AND_EQUIP_ITEM: _buy_and_equip_item,
    commands.CMD_VEH_SETTINGS: _vehicle_settings,
    commands.CMD_REQ_SERVER_STATS: _server_stats,
    commands.CMD_SYNC_SHOP: _sync_shop,
    commands.CMD_SYNC_DOSSIERS: _sync_dossiers,
    commands.CMD_ENQUEUE_RANDOM: _enqueue_random,
    commands.CMD_DEQUEUE_RANDOM: _dequeue_random,
    commands.CMD_SET_LANGUAGE: _set_language,
    commands.CMD_COMPLETE_TUTORIAL: lambda context, args: Result(commands.RES_SUCCESS),
    commands.CMD_ADD_INT_USER_SETTINGS: _add_int_user_settings,
    commands.CMD_DEL_INT_USER_SETTINGS: _del_int_user_settings,
}


def _payload_shape(args):
    """Describe the argument shape without dumping its contents."""
    parts = []
    for value in args:
        if isinstance(value, (list, tuple)):
            parts.append('%s[%d]' % (type(value).__name__, len(value)))
        else:
            parts.append(type(value).__name__)
    return '(%s)' % ', '.join(parts)


def _log_rejection(command, handler, message, args):
    sys.stdout.write(
        '[Offline LAN 0.9.22] command %d rejected by %s: %s; payload %s\n'
        % (command, handler, message or 'no reason given',
           _payload_shape(args)))


def dispatch(command, context, args):
    command = int(command)
    args = tuple(args or ())
    handler = HANDLERS.get(command)
    if handler is None:
        _log_rejection(command, 'dispatch', 'UNSUPPORTED_OFFLINE_COMMAND', args)
        return Result(commands.RES_FAILURE, 'UNSUPPORTED_OFFLINE_COMMAND')
    result = handler(context, args)
    if result.result_id == commands.RES_FAILURE:
        _log_rejection(
            command, getattr(handler, '__name__', repr(handler)),
            result.error, args)
    return result
