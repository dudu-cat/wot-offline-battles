"""Create privacy-bounded reports for one launcher game session.

The pinned client appends its player and worker output to two distinct files.
The launcher records exact byte offsets before either process starts and
freezes the end offsets when the session ends.  It never discovers a session
by timestamp or copies unrelated launcher/game state.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import stat
import subprocess
import uuid
import zipfile

try:
    from . import core
except ImportError:
    import core


SESSION_SCHEMA = 1
SESSION_STATE_FILENAME = "latest-error-report-session.json"
REPORTS_DIRECTORY_NAME = "reports"
SESSION_LOGS_DIRECTORY_NAME = "session-logs"
SERVER_SESSION_ENV = "WOT_OFFLINE_REPORT_SESSION"

ROLE_SERVER = "server"
ROLE_VISIBLE_CLIENT = "visible-client"
ROLE_HIDDEN_WORKER = "hidden-worker"
ROLE_HIDDEN_WORKER_STARTER = "hidden-worker-starter"

PRIMARY_ROLES = (
    ROLE_SERVER,
    ROLE_VISIBLE_CLIENT,
    ROLE_HIDDEN_WORKER,
)
_SOURCE_ORDER = (
    ROLE_SERVER,
    ROLE_VISIBLE_CLIENT,
    ROLE_HIDDEN_WORKER,
    ROLE_HIDDEN_WORKER_STARTER,
)
_GAME_LOG_FILENAMES = {
    ROLE_VISIBLE_CLIENT: "offline-player-python.log",
    ROLE_HIDDEN_WORKER: "offline-worker-python.log",
    ROLE_HIDDEN_WORKER_STARTER: core.WORKER_FAILURE_LOG_FILENAME_0922,
}
_ARCHIVE_FILENAMES = {
    ROLE_SERVER: "server.log",
    ROLE_VISIBLE_CLIENT: "visible-client.log",
    ROLE_HIDDEN_WORKER: "hidden-worker.log",
    ROLE_HIDDEN_WORKER_STARTER: "hidden-worker-starter.log",
}
_SESSION_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_CHUNK_BYTES = 64 * 1024


def _application_directory():
    return os.path.dirname(os.path.abspath(core.settings_path()))


def session_state_path():
    return os.path.join(_application_directory(), SESSION_STATE_FILENAME)


def reports_directory():
    return os.path.join(_application_directory(), REPORTS_DIRECTORY_NAME)


def session_logs_directory():
    return os.path.join(
        _application_directory(), SESSION_LOGS_DIRECTORY_NAME)


def _valid_session_id(session_id):
    value = str(session_id or "")
    if _SESSION_ID.fullmatch(value) is None:
        raise core.LauncherError("The diagnostic session identifier is invalid.")
    return value


def session_server_log_path(session_id):
    session_id = _valid_session_id(session_id)
    return os.path.join(
        session_logs_directory(), session_id, core.SERVER_LOG_FILENAME)


def _prepare_session_server_directory(session_id):
    directory = os.path.dirname(session_server_log_path(session_id))
    base = session_logs_directory()
    for candidate in (base, directory):
        if os.path.lexists(candidate):
            value = os.lstat(candidate)
            if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                raise core.LauncherError(
                    "The diagnostic server log folder is not a regular "
                    "directory.")
        else:
            os.makedirs(candidate)
    if os.path.commonpath((os.path.realpath(base), os.path.realpath(directory))
                          ) != os.path.realpath(base):
        raise core.LauncherError(
            "The diagnostic server log folder is unsafe.")
    return directory


def server_log_for_environment(environment=None):
    environment = os.environ if environment is None else environment
    session_id = environment.get(SERVER_SESSION_ENV)
    if not session_id:
        return core.server_log_path()
    _prepare_session_server_directory(session_id)
    return session_server_log_path(session_id)


def _file_identity(file_stat):
    device = int(getattr(file_stat, "st_dev", 0) or 0)
    inode = int(getattr(file_stat, "st_ino", 0) or 0)
    return [device, inode] if device or inode else None


def _checkpoint(path, kind):
    source = {
        "kind": kind,
        "offset": 0,
        "existed": False,
        "identity": None,
        "blocked": False,
    }
    try:
        value = os.lstat(path)
    except (IOError, OSError):
        return source
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        source["blocked"] = True
        return source
    source.update({
        "offset": int(value.st_size),
        "existed": True,
        "identity": _file_identity(value),
    })
    return source


def _write_state(session):
    path = session_state_path()
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(session, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def _read_state():
    try:
        with open(session_state_path(), "r", encoding="utf-8") as stream:
            session = json.load(stream)
    except (IOError, OSError, ValueError):
        return None
    return session if isinstance(session, dict) else None


def _new_session_id(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "%s-%s" % (stamp, uuid.uuid4().hex[:12])


def begin_session(game_root, needs_worker=False, local_server=False,
                  session_id=None, started_at=None):
    """Replace the report boundary before any process for a game starts."""
    game_root = os.path.realpath(os.path.abspath(game_root))
    session_id = _valid_session_id(session_id or _new_session_id())
    started_at = started_at or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    expected = [ROLE_VISIBLE_CLIENT]
    if local_server:
        expected.append(ROLE_SERVER)
    if needs_worker:
        expected.append(ROLE_HIDDEN_WORKER)
    sources = {
        ROLE_VISIBLE_CLIENT: _checkpoint(
            os.path.join(game_root, _GAME_LOG_FILENAMES[ROLE_VISIBLE_CLIENT]),
            "game"),
    }
    if needs_worker:
        for role in (ROLE_HIDDEN_WORKER, ROLE_HIDDEN_WORKER_STARTER):
            sources[role] = _checkpoint(
                os.path.join(game_root, _GAME_LOG_FILENAMES[role]), "game")
    session = {
        "schema": SESSION_SCHEMA,
        "id": session_id,
        "gameRoot": game_root,
        "startedAt": str(started_at),
        "endedAt": None,
        "expectedRoles": sorted(expected),
        "sources": sources,
    }
    _write_state(session)
    return session


def _is_latest(session):
    current = _read_state()
    return bool(current and current.get("id") == session.get("id"))


def attach_server(session, dedicated=False):
    """Attach either this session's new server or a reused persistent one."""
    if not _is_latest(session):
        return None
    if dedicated:
        _prepare_session_server_directory(session["id"])
        path = session_server_log_path(session["id"])
        kind = "session-server"
    else:
        path = core.server_log_path()
        kind = "launcher-server"
    session.setdefault("sources", {})[ROLE_SERVER] = _checkpoint(path, kind)
    _write_state(session)
    return path


def expect_worker_starter_reset(session):
    """Record that the launched native starter will delete its old log."""
    if not _is_latest(session):
        return False
    source = session.get("sources", {}).get(ROLE_HIDDEN_WORKER_STARTER)
    if source is None:
        return False
    source["resetExpected"] = True
    _write_state(session)
    return True


def _source_path(session, role, source):
    kind = source.get("kind")
    if kind == "game" and role in _GAME_LOG_FILENAMES:
        return os.path.join(
            session["gameRoot"], _GAME_LOG_FILENAMES[role])
    if kind == "launcher-server" and role == ROLE_SERVER:
        return core.server_log_path()
    if kind == "session-server" and role == ROLE_SERVER:
        return session_server_log_path(session["id"])
    return None


def _same_identity(expected, actual):
    return expected is None or list(expected) == list(actual or ())


def _freeze_source(session, role, source):
    source["finalized"] = True
    source["invalidated"] = False
    source["end"] = int(source.get("offset", 0))
    if source.get("blocked"):
        source["invalidated"] = True
        return
    path = _source_path(session, role, source)
    if path is None:
        source["invalidated"] = True
        return
    try:
        value = os.lstat(path)
    except (IOError, OSError):
        if source.get("existed"):
            source["invalidated"] = True
        return
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        source["invalidated"] = True
        return
    identity = _file_identity(value)
    offset = int(source.get("offset", 0))
    if (source.get("existed") and
            not _same_identity(source.get("identity"), identity)):
        if source.get("resetExpected"):
            source["offset"] = 0
            source["existed"] = False
            source["identity"] = None
            offset = 0
        else:
            source["invalidated"] = True
            return
    if int(value.st_size) < offset:
        source["invalidated"] = True
        return
    source["end"] = int(value.st_size)
    source["finalIdentity"] = identity


def finalize_session(session, ended_at=None):
    """Freeze exact end offsets so a persistent server cannot leak later data."""
    if not _is_latest(session):
        return False
    for role, source in session.get("sources", {}).items():
        _freeze_source(session, role, source)
    session["endedAt"] = str(ended_at or datetime.datetime.now(
        datetime.timezone.utc).isoformat())
    _write_state(session)
    return True


def _validated_session():
    session = _read_state()
    if session is None:
        raise core.LauncherError(
            "No launcher game session is available to report yet.")
    if (session.get("schema") != SESSION_SCHEMA or
            _SESSION_ID.fullmatch(str(session.get("id") or "")) is None or
            not isinstance(session.get("gameRoot"), str) or
            not os.path.isabs(session["gameRoot"]) or
            not isinstance(session.get("sources"), dict)):
        raise core.LauncherError(
            "The latest diagnostic session boundary is unreadable.")
    expected = session.get("expectedRoles")
    if (not isinstance(expected, list) or
            any(role not in PRIMARY_ROLES for role in expected)):
        raise core.LauncherError(
            "The latest diagnostic session boundary is unreadable.")
    if any(role not in _SOURCE_ORDER or not isinstance(source, dict)
           for role, source in session["sources"].items()):
        raise core.LauncherError(
            "The latest diagnostic session boundary is unreadable.")
    return session


def _open_valid_source(session, role, source):
    if source.get("blocked") or source.get("invalidated"):
        return None
    path = _source_path(session, role, source)
    if path is None:
        return None
    try:
        path_stat = os.lstat(path)
        if (stat.S_ISLNK(path_stat.st_mode) or
                not stat.S_ISREG(path_stat.st_mode)):
            return None
        stream = open(path, "rb")
    except (IOError, OSError):
        return None
    try:
        value = os.fstat(stream.fileno())
        if (not stat.S_ISREG(value.st_mode) or
                not _same_identity(
                    _file_identity(path_stat), _file_identity(value))):
            stream.close()
            return None
        identity = _file_identity(value)
        expected_identity = (source.get("identity")
                             if source.get("existed")
                             else source.get("finalIdentity"))
        identity_changed = not _same_identity(expected_identity, identity)
        if identity_changed and not source.get("resetExpected"):
            stream.close()
            return None
        start = (0 if identity_changed and source.get("resetExpected")
                 else int(source.get("offset", 0)))
        end = (int(source.get("end", start))
               if source.get("finalized") else int(value.st_size))
        if start < 0 or end <= start or end > int(value.st_size):
            stream.close()
            return None
        stream.seek(start)
        return stream, end - start
    except Exception:
        stream.close()
        return None


def _write_slice(archive, archive_name, stream, length):
    remaining = int(length)
    with archive.open(archive_name, "w") as target:
        while remaining:
            payload = stream.read(min(_CHUNK_BYTES, remaining))
            if not payload:
                raise IOError("A diagnostic log changed while it was copied.")
            target.write(payload)
            remaining -= len(payload)


def _prepare_reports_directory():
    directory = reports_directory()
    if os.path.lexists(directory):
        if os.path.islink(directory) or not os.path.isdir(directory):
            raise core.LauncherError(
                "The error report folder is not a regular directory.")
    else:
        os.makedirs(directory)
    return directory


def create_report(now=None):
    """Zip only new log bytes belonging to the latest explicit session."""
    session = _validated_session()
    directory = _prepare_reports_directory()
    now = now or datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    filename = "wot-error-report-%s-%s.zip" % (
        timestamp, session["id"].rsplit("-", 1)[-1])
    report_path = os.path.join(directory, filename)
    temporary = report_path + ".tmp-" + uuid.uuid4().hex
    included_roles = []
    included_files = []
    try:
        with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED,
                allowZip64=True) as archive:
            for role in _SOURCE_ORDER:
                source = session["sources"].get(role)
                if source is None:
                    continue
                opened = _open_valid_source(session, role, source)
                if opened is None:
                    continue
                stream, length = opened
                try:
                    archive_name = _ARCHIVE_FILENAMES[role]
                    _write_slice(archive, archive_name, stream, length)
                finally:
                    stream.close()
                included_roles.append(role)
                included_files.append(archive_name)
        if not included_files:
            raise core.LauncherError(
                "The latest game session has not produced any diagnostic "
                "logs yet. No earlier session was included.")
        os.replace(temporary, report_path)
    except Exception:
        try:
            os.unlink(temporary)
        except (IOError, OSError):
            pass
        raise
    expected = set(session["expectedRoles"])
    included_primary = set(included_roles).intersection(PRIMARY_ROLES)
    return {
        "path": report_path,
        "included": tuple(included_files),
        "missing": tuple(
            _ARCHIVE_FILENAMES[role] for role in PRIMARY_ROLES
            if role in expected and role not in included_primary),
        "notRun": tuple(
            _ARCHIVE_FILENAMES[role] for role in PRIMARY_ROLES
            if role not in expected),
    }


def select_in_explorer(report_path, runner=None):
    """Ask Windows Explorer to select the newly created archive."""
    report_path = os.path.abspath(report_path)
    if os.path.islink(report_path) or not os.path.isfile(report_path):
        raise core.LauncherError("The error report ZIP is missing.")
    runner = subprocess.Popen if runner is None else runner
    try:
        return runner(
            ["explorer.exe", "/select,", os.path.normpath(report_path)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (IOError, OSError) as error:
        raise core.LauncherError(
            "Windows Explorer could not select the error report: %s" % error)
