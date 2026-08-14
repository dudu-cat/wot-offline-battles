"""Small reversible fixes for stock #1513 lobby-only presentation."""


def _auto_announcement_due(controller):
    """Mirror the exact #1513 ChinaController automatic-open condition."""
    from gui.game_control import gc_constants

    return (controller.gameSession.battlesCount %
            gc_constants.BROWSER.CHINA_BROWSER_COUNT == 0)


def _load_announcement_runtime():
    from gui.game_control.ChinaController import ChinaController
    return ChinaController


class ServerAnnouncementUI(object):
    """Suppress only the CN server browser opened automatically at login.

    Exact build #1513's ``ChinaController.onLobbyInited`` contains only the
    modulo check and a call to ``showBrowser``.  A new offline account reports
    zero battles, so the stock automatic announcement would open even though
    there is no announcement service.  Suppressing that one entry callback is
    safer than observing the asynchronous browser ID: an explicit browser
    opened by the player can then never be mistaken for the announcement.

    ``showBrowser`` and BrowserController remain untouched.  Non-automatic
    lobby calls, failed condition checks and later wrappers keep their normal
    behavior, and uninstall restores only this adapter's own class patch.
    """

    def __init__(self, runtime=None, auto_due=None):
        self._runtime = runtime
        self._auto_due = auto_due or _auto_announcement_due
        self._installed = False
        self._controller_type = None
        self._original_lobby_inited = None
        self._had_own_lobby_inited = False
        self._lobby_inited_wrapper = None

    def install(self):
        if self._installed:
            return
        controller_type = self._runtime or _load_announcement_runtime()
        self._controller_type = controller_type
        self._had_own_lobby_inited = (
            'onLobbyInited' in controller_type.__dict__)
        self._original_lobby_inited = controller_type.__dict__.get(
            'onLobbyInited', getattr(controller_type, 'onLobbyInited'))
        adapter = self

        def wrapped_lobby_inited(controller, event):
            try:
                automatic_open_due = bool(adapter._auto_due(controller))
            except Exception:
                # This is presentation-only.  An unexpected client or another
                # mod's controller must still receive the stock lobby call.
                automatic_open_due = False
            if automatic_open_due:
                return None
            return adapter._original_lobby_inited(controller, event)

        self._lobby_inited_wrapper = wrapped_lobby_inited
        self._installed = True
        try:
            controller_type.onLobbyInited = wrapped_lobby_inited
        except Exception:
            self.uninstall()
            raise

    def _restore(self):
        current = self._controller_type.__dict__.get('onLobbyInited')
        if current is not self._lobby_inited_wrapper:
            return
        if self._had_own_lobby_inited:
            self._controller_type.onLobbyInited = self._original_lobby_inited
        else:
            delattr(self._controller_type, 'onLobbyInited')

    def uninstall(self):
        if not self._installed:
            return
        self._installed = False
        self._restore()
