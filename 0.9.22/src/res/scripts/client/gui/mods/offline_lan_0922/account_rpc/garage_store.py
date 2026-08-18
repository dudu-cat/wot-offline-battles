"""Persist the offline garage beside the other user-owned configuration.

Owner boundary: ``AccountState`` owns ``account_state.json`` and nothing else,
so the garage keeps a sibling ``garage_state.json``.  One file per owner means a
corrupt garage file cannot take out the saved interface settings, and neither
writer has to understand the other's schema.

Why the keys are not inventory ids: ``bootstrap._selected_vehicle`` numbers
vehicles ``len(vehicle_records) + 1`` while walking a type list whose FIRST
entry is the vehicle named in ``config.json``, and it numbers crew from 100001
upward in that same order.  Changing the configured vehicle therefore renumbers
every id.  This store keys vehicles on ``vehicleTypeCompactDescr`` and crew on
the slot index inside their vehicle, both of which survive a renumbering.

Compact descriptors are Python 2 byte strings, so they are base64 text on disk.
"""

from __future__ import print_function

import base64
import os
import sys

from gui.mods.offline_lan_0922 import config as port_config


SCHEMA = 1
STATE_PATH = os.path.join(
    os.path.dirname(port_config.CONFIG_PATH), 'garage_state.json')

_VEHICLE_INT_KEYS = ('eqs', 'eqsLayout', 'shells')
_ARTEFACT_ITEM_TYPES = (9, 10, 11)


def _log(message):
    sys.stdout.write('[Offline LAN 0.9.22] %s\n' % message)


def _encode_bytes(value):
    if isinstance(value, bytes):
        return base64.b64encode(value).decode('ascii')
    return None


def _decode_bytes(value):
    try:
        return base64.b64decode(value.encode('ascii'))
    except Exception:
        return None


def _int_list(value):
    if not isinstance(value, (list, tuple)):
        return None
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        result.append(int(item))
    return result


def _int_map(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for raw_key, raw_value in value.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            return None
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            return None
        result[key] = int(raw_value)
    return result


class GarageStore(object):
    """Load and save the mutable parts of one garage snapshot."""

    def __init__(self, path=STATE_PATH):
        self._path = path
        self._dirty = False

    # ---- writing --------------------------------------------------------

    def mark_dirty(self):
        self._dirty = True

    def flush(self, snapshot):
        """Write the snapshot if a mutation is pending.

        Fittings happen at click speed, so writing on each accepted change is
        cheap and means a hard client kill cannot lose an applied change.
        """
        if not self._dirty or self._path is None:
            return False
        payload = self._payload(snapshot)
        try:
            port_config.write_json(self._path, payload)
        except (IOError, OSError) as error:
            _log('the garage state could not be saved: %s' % error)
            return False
        self._dirty = False
        return True

    def _payload(self, snapshot):
        vehicles = {}
        for record in _records(snapshot):
            key = record.get('vehicleTypeCompactDescr')
            if key is None:
                continue
            stored = {}
            compact_descr = _encode_bytes(record.get('compDescr'))
            if compact_descr is not None:
                stored['compDescr'] = compact_descr
            for name in _VEHICLE_INT_KEYS:
                value = _int_list(record.get(name))
                if value is not None:
                    stored[name] = value
            layout = _int_map(record.get('shellsLayout'))
            if layout is not None:
                stored['shellsLayout'] = dict(
                    (str(shell), count) for shell, count in layout.items())
            try:
                stored['settings'] = int(record.get('settings', 0) or 0)
            except (TypeError, ValueError):
                stored['settings'] = 0
            crew = {}
            tankmen = record.get('tankmen')
            order = list(record.get('crew') or ())
            if isinstance(tankmen, dict):
                for slot, tankman_id in enumerate(order):
                    encoded = _encode_bytes(tankmen.get(tankman_id))
                    if encoded is not None:
                        crew[str(slot)] = encoded
            if crew:
                stored['crew'] = crew
            vehicles[str(int(key))] = stored

        owned = {}
        published = snapshot.get('inventoryItems')
        if isinstance(published, dict):
            for item_type, items in published.items():
                try:
                    item_type = int(item_type)
                except (TypeError, ValueError):
                    continue
                if item_type not in _ARTEFACT_ITEM_TYPES:
                    continue
                counts = _int_map(items)
                if counts:
                    owned[str(item_type)] = dict(
                        (str(compact_descr), count)
                        for compact_descr, count in counts.items())
        return {'schema': SCHEMA, 'vehicles': vehicles, 'owned': owned}

    # ---- reading --------------------------------------------------------

    def apply(self, snapshot):
        """Overlay the saved garage onto a freshly built bootstrap snapshot.

        The snapshot always comes from the current client, so an unknown or
        stale key is skipped rather than trusted.  Any problem falls back to the
        bootstrap snapshot untouched: an unusable garage is worse than a lost
        fitting.
        """
        stored = self._read()
        if stored is None:
            return False
        vehicles = stored.get('vehicles')
        if not isinstance(vehicles, dict):
            vehicles = {}
        applied = 0
        for record in _records(snapshot):
            key = record.get('vehicleTypeCompactDescr')
            if key is None:
                continue
            saved = vehicles.get(str(int(key)))
            if isinstance(saved, dict) and self._apply_vehicle(record, saved):
                applied += 1

        owned = stored.get('owned')
        if isinstance(owned, dict):
            published = snapshot.setdefault('inventoryItems', {})
            prices = snapshot.setdefault('shopItemPrices', {})
            for raw_type, items in owned.items():
                try:
                    item_type = int(raw_type)
                except (TypeError, ValueError):
                    continue
                if item_type not in _ARTEFACT_ITEM_TYPES:
                    continue
                counts = _int_map(items)
                if not counts:
                    continue
                target = published.setdefault(item_type, {})
                for compact_descr, count in counts.items():
                    target[compact_descr] = max(
                        int(target.get(compact_descr, 0)), int(count))
                    prices.setdefault(
                        compact_descr, {'credits': 0, 'gold': 0})
        if applied:
            _log('restored the saved garage for %d vehicle(s)' % applied)
        return True

    def _apply_vehicle(self, record, saved):
        changed = False
        compact_descr = saved.get('compDescr')
        if isinstance(compact_descr, str):
            decoded = _decode_bytes(compact_descr)
            if decoded:
                record['compDescr'] = decoded
                changed = True
        for name in _VEHICLE_INT_KEYS:
            value = _int_list(saved.get(name))
            if value is not None:
                record[name] = value
                changed = True
        layout = _int_map(saved.get('shellsLayout'))
        if layout is not None:
            record['shellsLayout'] = layout
            changed = True
        if 'settings' in saved:
            try:
                record['settings'] = int(saved['settings'])
                changed = True
            except (TypeError, ValueError):
                pass
        crew = saved.get('crew')
        tankmen = record.get('tankmen')
        order = list(record.get('crew') or ())
        if isinstance(crew, dict) and isinstance(tankmen, dict):
            for raw_slot, encoded in crew.items():
                try:
                    slot = int(raw_slot)
                except (TypeError, ValueError):
                    continue
                if not 0 <= slot < len(order):
                    continue
                decoded = _decode_bytes(encoded) if isinstance(
                    encoded, str) else None
                if decoded:
                    tankmen[order[slot]] = decoded
                    changed = True
        # Mounted shells must stay consistent with the shell inventory that
        # data._validate_selected_vehicle cross-checks.
        shells = _int_list(record.get('shells'))
        if shells is not None and not len(shells) % 2:
            pairs = {}
            for index in range(0, len(shells), 2):
                pairs[shells[index]] = shells[index + 1]
            record.setdefault('inventoryItems', {})[10] = pairs
        return changed

    def _read(self):
        if self._path is None:
            return None
        for path in (self._path, self._path + '.bak'):
            if not os.path.isfile(path):
                continue
            try:
                import json
                with open(path, 'rb') as stream:
                    value = json.load(stream)
            except (IOError, OSError, ValueError):
                _log('the saved garage state is unreadable; using the '
                     'stock garage')
                continue
            if not isinstance(value, dict):
                _log('the saved garage state has an unexpected shape; using '
                     'the stock garage')
                continue
            if value.get('schema') != SCHEMA:
                _log('the saved garage state uses schema %r, not %d; using '
                     'the stock garage' % (value.get('schema'), SCHEMA))
                return None
            return value
        return None


def _records(snapshot):
    if not isinstance(snapshot, dict):
        return []
    records = snapshot.get('vehicles')
    if isinstance(records, (list, tuple)) and records:
        return [record for record in records if isinstance(record, dict)]
    return [snapshot] if snapshot.get('compDescr') else []
