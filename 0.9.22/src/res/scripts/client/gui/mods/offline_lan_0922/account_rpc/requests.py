"""Registered request handlers only; unknown command ids deliberately fail."""

from __future__ import print_function

import traceback

from gui.mods.offline_lan_0922.account_rpc import commands
from gui.mods.offline_lan_0922.account_rpc import data


class Result(object):
    def __init__(self, result_id, error='', stream=None, ext=None,
                 before_response=None):
        self.result_id = result_id
        self.error = error
        self.stream = stream
        self.ext = ext
        self.before_response = before_response


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


def dispatch(command, context, args):
    handler = HANDLERS.get(int(command))
    if handler is None:
        return Result(commands.RES_FAILURE, 'UNSUPPORTED_OFFLINE_COMMAND')
    return handler(context, tuple(args or ()))
