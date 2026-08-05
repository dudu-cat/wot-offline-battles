"""Asynchronous Account base proxy matching #1513 response/stream callbacks."""

try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle
import zlib

from gui.mods.offline_lan_0922.account_rpc import commands, requests


def pack_stream(value):
    payload = zlib.compress(_pickle.dumps(value, _pickle.HIGHEST_PROTOCOL))
    crc = zlib.crc32(payload) & 0xffffffff
    return (False, len(payload), len(payload), crc, crc), payload


class FakeServer(object):
    """Base/cell stand-in with registered command dispatch and async callbacks.

    ``callback`` must behave as ``BigWorld.callback(0.0, fn)``.  In production
    it defaults to BigWorld.callback; tests can inject a deterministic queue.
    """
    def __init__(self, player_getter, callback=None, context=None):
        self._player_getter = player_getter
        self._context = dict(context or {})
        if self._context.get('account_state') is None:
            from gui.mods.offline_lan_0922.account_rpc.state import AccountState
            self._context['account_state'] = AccountState(path=None)
        if callback is None:
            import BigWorld
            callback = BigWorld.callback
        self._callback = callback

    def _player(self):
        try:
            return self._player_getter()
        except ReferenceError:
            return None

    def _respond(self, request_id, command, args):
        result = requests.dispatch(command, self._context, args)

        def before_response():
            if callable(result.before_response):
                result.before_response()

        if result.result_id == commands.RES_STREAM:
            desc, payload = pack_stream(result.stream)

            def respond_then_stream():
                player = self._player()
                if player is None:
                    return
                before_response()
                player.onCmdResponse(request_id, result.result_id, result.error)

                def stream():
                    if self._player() is player:
                        player.onStreamComplete(
                            request_id, desc, payload)

                self._callback(0.0, stream)

            self._callback(0.0, respond_then_stream)
        elif result.ext is not None:
            ext = _pickle.dumps(result.ext, _pickle.HIGHEST_PROTOCOL)

            def respond_ext():
                player = self._player()
                if player is None:
                    return
                before_response()
                player.onCmdResponseExt(
                    request_id, result.result_id, result.error, ext)

            self._callback(0.0, respond_ext)
        else:
            def respond():
                player = self._player()
                if player is None:
                    return
                before_response()
                player.onCmdResponse(
                    request_id, result.result_id, result.error)

            self._callback(0.0, respond)
        return request_id

    def doCmdStr(self, request_id, command, value):
        return self._respond(request_id, command, (value,))

    def doCmdInt3(self, request_id, command, first, second, third):
        return self._respond(request_id, command, (first, second, third))

    def doCmdIntArr(self, request_id, command, values):
        return self._respond(request_id, command, (values,))

    def doCmdInt2Str(self, request_id, command, first, second, value):
        return self._respond(request_id, command, (first, second, value))

    def doCmdIntStr(self, request_id, command, first, value):
        return self._respond(request_id, command, (first, value))

    def doCmdInt4(self, request_id, command, first, second, third, fourth):
        return self._respond(request_id, command,
                             (first, second, third, fourth))

    def doCmdIntStrArr(self, request_id, command, first, values):
        return self._respond(request_id, command, (first, values))

    def doCmdIntArrStrArr(self, request_id, command, ints, strings):
        return self._respond(request_id, command, (ints, strings))

    def chatCommandFromClient(self, request_id, action, channel_id,
                              int64_arg, int16_arg, string_arg1,
                              string_arg2):
        # This is a one-way mailbox.  Empty offline chat has no server action
        # to publish.  In particular, ``action`` is a CHAT_COMMANDS index, not
        # a CHAT_ACTIONS index; echoing it into ClientChat.onChatAction causes
        # its exact #1513 processors to index malformed data before dispatch.
        return True

    def messenger_onActionByClient_chat2(self, action_id, request_id, args):
        """Accept the enabled #1513 BW_CHAT2 provider's one-way mailbox."""
        return True

    def update_context(self, values):
        if isinstance(values, dict):
            self._context.update(values)
