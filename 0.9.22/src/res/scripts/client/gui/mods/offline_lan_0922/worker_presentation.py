from __future__ import print_function

"""Reversible sound and native-window isolation for the simulation worker."""

import os


WORKER_READY_MARKER_ENV = 'OFFLINE_LAN_0922_WORKER_READY_MARKER'
HIDDEN_DESKTOP_ENV = 'OFFLINE_LAN_0922_HIDDEN_DESKTOP'

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)


class WorkerPresentationError(RuntimeError):
    pass


def signal_worker_ready(environ=None):
    """Atomically publish full worker Hangar and LAN readiness."""
    environ = os.environ if environ is None else environ
    marker_path = environ.get(WORKER_READY_MARKER_ENV, '')
    if not marker_path:
        raise WorkerPresentationError(
            'simulation worker ready marker is unavailable')
    temporary_path = marker_path + '.tmp'
    try:
        os.remove(temporary_path)
    except OSError:
        pass
    try:
        stream = open(temporary_path, 'wb')
        try:
            stream.write(b'ready\n')
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            stream.close()
        try:
            os.remove(marker_path)
        except OSError:
            pass
        os.rename(temporary_path, marker_path)
    except Exception:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise
    return True


def _load_runtime():
    import SoundGroups
    import WWISE
    import offline_instance_guard_native

    return offline_instance_guard_native, WWISE, SoundGroups.g_instance


class WorkerPresentation(object):
    """Keep the second client silent and out of the player's desktop.

    The native bridge remembers the placement of only the visible top-level
    windows it hides.  The WWISE wrapper forces every later stock preference
    re-application to keep the worker muted without changing that profile's
    saved sound settings.  A worker startup failure keeps every isolation step
    already established in place while the process exits; explicit restoration
    remains available only for a caller that keeps the process alive.
    """

    def __init__(self, runtime=None, environ=None):
        self._runtime = runtime
        self._environ = os.environ if environ is None else environ
        self._native = None
        self._wwise = None
        self._sound_groups = None
        self._original_set_master_volume = None
        self._original_master_volume = None
        self._mute_wrapper = None
        self._window_hidden = False
        self._active = False

    @property
    def active(self):
        return self._active

    @staticmethod
    def _read_master_volume(sound_groups):
        value = getattr(
            sound_groups, '_SoundGroups__masterVolume', None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorkerPresentationError(
                'WWISE master-volume state is unavailable')
        return float(value)

    def activate(self):
        if self._active:
            return True
        native, wwise, sound_groups = self._runtime or _load_runtime()
        hide_windows = getattr(native, 'hide_process_windows', None)
        show_windows = getattr(native, 'show_process_windows', None)
        set_master_volume = getattr(wwise, 'WW_setMasterVolume', None)
        private_desktop = (
            self._environ.get(HIDDEN_DESKTOP_ENV, '') == '1')
        if (not private_desktop and
                (not callable(hide_windows) or not callable(show_windows))):
            raise WorkerPresentationError(
                'native worker-window isolation is unavailable')
        if not callable(set_master_volume):
            raise WorkerPresentationError('WWISE master volume is unavailable')

        self._native = native
        self._wwise = wwise
        self._sound_groups = sound_groups
        self._original_set_master_volume = set_master_volume
        self._original_master_volume = self._read_master_volume(sound_groups)

        original = set_master_volume

        def force_silent(unused_volume):
            return original(0.0)

        self._mute_wrapper = force_silent
        wwise.WW_setMasterVolume = force_silent
        original(0.0)
        if not private_desktop:
            hidden_count = hide_windows()
            if (isinstance(hidden_count, bool) or
                    not isinstance(hidden_count, _INTEGER_TYPES) or
                    hidden_count < 1):
                raise WorkerPresentationError(
                    'native worker window was not found')
            self._window_hidden = True
        self._active = True
        return True

    def _restore_audio(self):
        wwise = self._wwise
        original = self._original_set_master_volume
        wrapper = self._mute_wrapper
        if (wwise is None or original is None or
                getattr(wwise, 'WW_setMasterVolume', None) is not wrapper):
            return False
        wwise.WW_setMasterVolume = original
        original(self._original_master_volume)
        return True

    def _restore_window(self):
        native = self._native
        if native is None:
            return False
        show_windows = getattr(native, 'show_process_windows', None)
        if not callable(show_windows):
            raise WorkerPresentationError(
                'native worker-window restoration is unavailable')
        show_windows()
        self._window_hidden = False
        return True

    def _clear(self):
        self._active = False
        self._native = None
        self._wwise = None
        self._sound_groups = None
        self._original_set_master_volume = None
        self._original_master_volume = None
        self._mute_wrapper = None
        self._window_hidden = False

    def _rollback(self):
        errors = []
        if self._window_hidden:
            try:
                self._restore_window()
            except Exception as error:
                errors.append(error)
        try:
            self._restore_audio()
        except Exception as error:
            errors.append(error)
        self._clear()
        if errors:
            raise errors[0]
        return True

    def deactivate(self, restore=True):
        if (self._native is None and self._wwise is None and
                not self._active):
            return True
        if not restore:
            self._clear()
            return True
        return self._rollback()
