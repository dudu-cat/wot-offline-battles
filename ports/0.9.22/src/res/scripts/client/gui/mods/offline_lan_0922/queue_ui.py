from __future__ import print_function

from gui.mods.offline_lan_0922 import map_catalog


OFFLINE_PICKER_FLAG = 'isOfflineLanPicker'
_PICKER_MARKER = '_offline_lan_0922_picker'

# Hook shape adapted from the public 0.9.22 observer implementation:
# https://github.com/the-tuxedo-cat/wot-offline-server/blob/c0bc550c46deac980194b7b860ee8781d53ec97b/sources/scripts/client/gui/mods/mod_observer.py#L73-L80,L138-L139,L231-L244
# Its direct Avatar transition is deliberately not used here.  This adapter
# only replaces the map result with the existing LAN request_start boundary.
UPSTREAM_TUXEDO_COMMIT = 'c0bc550c46deac980194b7b860ee8781d53ec97b'
UPSTREAM_TUXEDO_URL = (
    'https://github.com/the-tuxedo-cat/wot-offline-server/blob/' +
    UPSTREAM_TUXEDO_COMMIT +
    '/sources/scripts/client/gui/mods/mod_observer.py')


def _load_runtime():
    import ArenaType
    from gui.Scaleform.daapi.view.lobby.trainings.TrainingSettingsWindow import \
        TrainingSettingsWindow
    return (ArenaType, TrainingSettingsWindow)


def open_picker():
    """Open the stock training settings view, following the observer hook."""
    from gui.Scaleform.framework.managers.loaders import ViewLoadParams
    from gui.Scaleform.genConsts.PREBATTLE_ALIASES import PREBATTLE_ALIASES
    from gui.app_loader import g_appLoader

    alias = PREBATTLE_ALIASES.TRAINING_SETTINGS_WINDOW_PY
    app = g_appLoader.getDefLobbyApp()
    if app is None:
        return False
    app.loadView(ViewLoadParams(alias, alias), {
        'isCreateRequest': True,
        OFFLINE_PICKER_FLAG: True,
    })
    return True


class QueueUI(object):
    """A reversible, chain-safe adapter for the stock map picker."""

    def __init__(self, request_start, map_pool, runtime=None):
        self._request_start = request_start
        self._map_pool = map_pool
        self._runtime = runtime
        self._installed = False
        self._window_type = None
        self._original_init = None
        self._original_update = None
        self._had_own_init = False
        self._had_own_update = False
        self._init_wrapper = None
        self._update_wrapper = None
        self._picker_window = None

    def install(self):
        if self._installed:
            return
        arena_type, window_type = self._runtime or _load_runtime()
        self._runtime = (arena_type, window_type)
        self._window_type = window_type
        self._had_own_init = '__init__' in window_type.__dict__
        self._had_own_update = 'updateTrainingRoom' in window_type.__dict__
        # In Python 2, getattr(class, method) returns a fresh unbound-method
        # wrapper.  Keep the raw class members so identity checks during
        # chain-safe uninstall remain meaningful on the target runtime.
        self._original_init = window_type.__dict__.get(
            '__init__', getattr(window_type, '__init__'))
        self._original_update = window_type.__dict__.get(
            'updateTrainingRoom', getattr(window_type, 'updateTrainingRoom'))
        adapter = self

        def wrapped_init(window, ctx=None):
            result = adapter._original_init(window, ctx)
            context = ctx or {}
            if bool(context.get(OFFLINE_PICKER_FLAG, False)):
                setattr(window, _PICKER_MARKER, True)
                adapter._picker_window = window
                catalog = map_catalog.build(arena_type.g_cache,
                                            adapter._map_pool())
                setattr(window, '_TrainingSettingsWindow__arenasCache',
                        catalog)
            return result

        def wrapped_update(window, arena, round_length, is_private, comment):
            if not getattr(window, _PICKER_MARKER, False):
                return adapter._original_update(
                    window, arena, round_length, is_private, comment)
            map_name = map_catalog.geometry_name(arena_type.g_cache, arena,
                                                 adapter._map_pool())
            if map_name is None:
                return False
            accepted = adapter._request_start(map_name)
            if accepted is False:
                return False
            if adapter._picker_window is window:
                adapter._picker_window = None
                setattr(window, _PICKER_MARKER, False)
                close = getattr(window, 'onWindowClose', None)
                if callable(close):
                    close()
            else:
                # LANSession.request_start closes the picker synchronously.
                # Do not invoke stock destroy() for the same window twice.
                setattr(window, _PICKER_MARKER, False)
            return True

        self._init_wrapper = wrapped_init
        self._update_wrapper = wrapped_update
        window_type.__init__ = wrapped_init
        window_type.updateTrainingRoom = wrapped_update
        self._installed = True

    def close(self):
        """Close only the stock window created by this LAN picker adapter."""
        window = self._picker_window
        self._picker_window = None
        if window is None:
            return False
        setattr(window, _PICKER_MARKER, False)
        close = getattr(window, 'onWindowClose', None)
        if callable(close):
            close()
        return True

    def _restore(self, name, original, installed, had_own):
        current = self._window_type.__dict__.get(name)
        if current is not installed:
            return
        if had_own:
            setattr(self._window_type, name, original)
        else:
            delattr(self._window_type, name)

    def uninstall(self):
        if not self._installed:
            return
        self._restore('__init__', self._original_init, self._init_wrapper,
                      self._had_own_init)
        self._restore('updateTrainingRoom', self._original_update,
                      self._update_wrapper, self._had_own_update)
        self._installed = False
        self._picker_window = None
