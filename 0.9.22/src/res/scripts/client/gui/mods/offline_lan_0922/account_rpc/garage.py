"""Mutable offline garage: fitting, ammunition layouts and crew skills.

The immutable snapshot that ``bootstrap._selected_vehicle`` builds is the
starting point.  This module keeps a mutable copy of exactly that shape, so a
full ``CMD_SYNC_DATA`` and a pushed diff both flow through the already
validated ``data.inventory`` shaping code instead of a second wire format.

Exact #1513 contracts used here, all from ``account_helpers/Inventory.pyc``:

- ``CMD_EQUIP_EQS`` carries ``[vehInvID] + [int(e) for e in eqs]``, where
  ``eqs`` is ``VehicleEquipment.getConsumablesIntCDs()``: three regular slots
  followed by the battle-booster slot;
- ``CMD_EQUIP_SHELLS`` carries ``[vehInvID] + [int(s) for s in shells]``;
- ``CMD_EQUIP_OPTDEV`` carries
  ``[shopRev, vehInvID, deviceCompDescr, slotIdx, int(isPaidRemoval)]``;
- ``CMD_SET_AND_FILL_LAYOUTS`` carries
  ``[shopRev, vehInvID, len(shellsLayout), *shellsLayout, equipmentType,
  len(eqsLayout), *eqsLayout]``, with a single ``0`` in place of a missing
  layout.  Both layouts are flat ``(compactDescr, count)`` pairs read by
  ``account_shared.LayoutIterator``, which takes ``abs(compactDescr)`` and
  reads the sign as "buy for the alternative price";
- ``CMD_TMAN_ADD_SKILL`` is a ``_doCmdInt3`` of ``(tmanInvID, skillIdx, 0)``.

Optional devices and modules live inside the vehicle's own compact descriptor,
so a mount rebuilds ``compDescr`` through ``VehicleDescr`` rather than storing a
parallel list.  This is why the 0.8.2 reference insists that the fitting and
customization writers share one live record: two independent writers would each
rebuild the descriptor from a stale copy and silently drop the other's change.
"""

import copy

EQUIPMENT_SLOT_COUNT = 3
# vehicles.NUM_EQUIPMENT_SLOTS in #1513: the three regular slots plus the
# battle-booster slot that every equipment payload still carries.
EQUIPMENT_PAYLOAD_SLOT_COUNT = 4
EQUIPMENT_TYPE_REGULAR = 0
OPTIONAL_DEVICE_ITEM_TYPE = 9
SHELL_ITEM_TYPE = 10
EQUIPMENT_ITEM_TYPE = 11


class GarageError(Exception):
    pass


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise GarageError('expected an integer, got %r' % (value,))


def mirror_shells_layout(record):
    """Publish the loaded shells as the vehicle's own ammunition layout.

    #1513 reads ``shellsLayout[(turretCompDescr, gunCompDescr)]`` and falls back
    to the gun's default ammo, then warns through ``Vehicle.isAutoLoadFull``
    when a loaded count differs from that layout.  Offline resupply is instant,
    so the layout is always exactly what is loaded.
    """
    key = record.get('shellsLayoutIdx')
    record['shellsLayout'] = (
        {tuple(key): list(record.get('shells') or ())} if key else {})


def _layout_pairs(values, slot_limit=None):
    """Decode a flat #1513 layout into ``(compactDescr, count)`` pairs."""
    values = [_int(value) for value in (values or ())]
    if len(values) % 2:
        raise GarageError('a layout must contain descriptor/count pairs')
    pairs = [(abs(values[index]), values[index + 1])
             for index in range(0, len(values), 2)]
    if slot_limit is not None and len(pairs) > slot_limit:
        raise GarageError('a layout carries at most %d slots' % slot_limit)
    return pairs


class GarageState(object):
    """One mutable garage snapshot shared by every account command."""

    def __init__(self, snapshot, vehicles_module=None, tankmen_module=None):
        if not isinstance(snapshot, dict):
            raise GarageError('garage snapshot must be a mapping')
        self._snapshot = copy.deepcopy(snapshot)
        self._vehicles = vehicles_module
        self._tankmen = tankmen_module
        self._touched = set()
        self._touched_items = {}
        self.revision = 0

    def snapshot(self):
        return self._snapshot

    def _vehicles_module(self):
        if self._vehicles is None:
            from items import vehicles
            self._vehicles = vehicles
        return self._vehicles

    def _tankmen_module(self):
        if self._tankmen is None:
            from items import tankmen
            self._tankmen = tankmen
        return self._tankmen

    def _records(self):
        records = self._snapshot.get('vehicles')
        if isinstance(records, (list, tuple)) and records:
            return list(records)
        return [self._snapshot] if self._snapshot.get('compDescr') else []

    def _record(self, vehicle_inventory_id):
        wanted = _int(vehicle_inventory_id)
        for record in self._records():
            if _int(record.get('id', 0)) == wanted:
                self._touched.add(wanted)
                return record
        raise GarageError('unknown vehicle inventory id %d' % wanted)

    def touched_vehicles(self):
        """Return the vehicle ids mutated since the last call, then reset."""
        touched = set(self._touched)
        self._touched = set()
        return touched

    def touched_items(self):
        """Return the owned items mutated since the last call, then reset."""
        touched = self._touched_items
        self._touched_items = {}
        return touched

    def _tankman_record(self, tankman_inventory_id):
        wanted = _int(tankman_inventory_id)
        for record in self._records():
            tankmen = record.get('tankmen')
            if isinstance(tankmen, dict) and wanted in tankmen:
                self._touched.add(_int(record.get('id', 0)))
                return record, wanted
        raise GarageError('unknown tankman inventory id %d' % wanted)

    def _own(self, record, compact_descr, item_type, count=1):
        """Own an item on the record and in the account-wide catalogue.

        ``data._validate_selected_vehicle`` requires the top-level catalogue to
        cover every per-record item at no less than the record's count, so both
        levels always move together.
        """
        if not compact_descr:
            return
        count = max(1, int(count))
        items = record.setdefault('inventoryItems', {})
        owned = items.setdefault(int(item_type), {})
        owned[compact_descr] = max(count, int(owned.get(compact_descr, 0)))
        self._publish_owned(compact_descr, item_type, owned[compact_descr])

    def _publish_owned(self, compact_descr, item_type, count):
        published = self._snapshot.setdefault('inventoryItems', {})
        owned = published.setdefault(int(item_type), {})
        owned[compact_descr] = max(int(count), int(owned.get(compact_descr, 0)))
        self._touched_items.setdefault(int(item_type), set()).add(compact_descr)

    # ---- ammunition -----------------------------------------------------

    def equip_shells(self, vehicle_inventory_id, shells):
        values = [_int(value) for value in (shells or ())]
        if len(values) % 2:
            raise GarageError('shells must be descriptor/count pairs')
        record = self._record(vehicle_inventory_id)
        record['shells'] = values
        mirror_shells_layout(record)
        # data._validate_selected_vehicle requires the shell inventory and the
        # flat pair list to agree, so both move together.
        pairs = {}
        for index in range(0, len(values), 2):
            pairs[values[index]] = values[index + 1]
        record.setdefault('inventoryItems', {})[SHELL_ITEM_TYPE] = pairs
        for compact_descr, count in pairs.items():
            self._publish_owned(compact_descr, SHELL_ITEM_TYPE, count)
            self._price(compact_descr)
        self.revision += 1
        return record

    def _price(self, compact_descr):
        prices = self._snapshot.setdefault('shopItemPrices', {})
        if compact_descr and compact_descr not in prices:
            prices[compact_descr] = {'credits': 0, 'gold': 0}
        unlocks = self._snapshot.get('unlockItemCompactDescrs')
        # Only extend a set that already lists the garage: an empty set means
        # the snapshot opted out of the unlock check, and partially filling it
        # would start enforcing a constraint on items nobody validated.
        if isinstance(unlocks, set) and unlocks and compact_descr:
            unlocks.add(compact_descr)

    # ---- consumables ----------------------------------------------------

    def equip_equipments(self, vehicle_inventory_id, equipments):
        """Mount the regular consumables of one equipment payload."""
        values = [_int(value) for value in (equipments or ())]
        if len(values) > EQUIPMENT_PAYLOAD_SLOT_COUNT:
            raise GarageError('an equipment payload carries at most four slots')
        # The trailing battle-booster slot has no published counterpart.
        values = values[:EQUIPMENT_SLOT_COUNT]
        values += [0] * (EQUIPMENT_SLOT_COUNT - len(values))
        record = self._record(vehicle_inventory_id)
        record['eqs'] = values
        # Offline resupply is instant, so the vehicle is always at its layout.
        # Vehicle.isAutoEquipFull compares the two and warns when they differ.
        record['eqsLayout'] = list(values)
        for compact_descr in values:
            self._own(record, compact_descr, 11)
            self._price(compact_descr)
        self.revision += 1
        return record

    def set_layouts(self, vehicle_inventory_id, shells_layout=None,
                    equipment_type=EQUIPMENT_TYPE_REGULAR,
                    equipments_layout=None):
        """Store one layout and load the vehicle to it.

        Offline stock is unlimited, so the "fill" half of the request is the
        mount itself: the client shows ``eqs`` and ``shells``, not the layout.
        """
        record = self._record(vehicle_inventory_id)
        if shells_layout is not None:
            flat = []
            for compact_descr, count in _layout_pairs(shells_layout):
                flat.extend((compact_descr, count))
            self.equip_shells(vehicle_inventory_id, flat)
        if (equipments_layout is not None and
                _int(equipment_type) == EQUIPMENT_TYPE_REGULAR):
            pairs = _layout_pairs(
                equipments_layout, EQUIPMENT_PAYLOAD_SLOT_COUNT)
            slots = [compact_descr
                     for compact_descr, unused_count in pairs
                     ][:EQUIPMENT_SLOT_COUNT]
            self.equip_equipments(vehicle_inventory_id, slots)
        self.revision += 1
        return record

    # ---- optional devices and modules -----------------------------------

    def _rebuild_descriptor(self, record, mutate):
        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(
                compactDescr=record['compDescr'])
        except Exception as error:
            raise GarageError('vehicle descriptor is unreadable: %s' % error)
        try:
            mutate(descriptor)
            record['compDescr'] = descriptor.makeCompactDescr()
            record['shellsLayoutIdx'] = (
                descriptor.turret.compactDescr, descriptor.gun.compactDescr)
        except Exception as error:
            raise GarageError('the client refused the fitting: %s' % error)
        mirror_shells_layout(record)
        return record

    def equip_optional_device(self, vehicle_inventory_id, device_compact_descr,
                              slot_index):
        record = self._record(vehicle_inventory_id)
        device_compact_descr = _int(device_compact_descr)
        slot_index = _int(slot_index)

        def mutate(descriptor):
            # Removing first makes a slot swap idempotent; #1513 rejects an
            # install into an occupied slot.
            try:
                descriptor.removeOptionalDevice(slot_index)
            except Exception:
                pass
            if device_compact_descr:
                descriptor.installOptionalDevice(
                    device_compact_descr, slot_index)

        self._rebuild_descriptor(record, mutate)
        self._own(record, device_compact_descr, 9)
        self._price(device_compact_descr)
        self.revision += 1
        return record

    def install_component(self, vehicle_inventory_id, compact_descr,
                          position_index=0):
        """Install a module: this is the gun, turret, engine or chassis swap."""
        record = self._record(vehicle_inventory_id)
        compact_descr = _int(compact_descr)
        position_index = _int(position_index)

        def mutate(descriptor):
            descriptor.installComponent(compact_descr, position_index)

        self._rebuild_descriptor(record, mutate)
        self._price(compact_descr)
        # A gun swap changes which shells fit, so the stale flat pair list and
        # the shell inventory would no longer agree.  Refill from the new gun.
        self._refill_default_ammo(record)
        self.revision += 1
        return record

    def _refill_default_ammo(self, record):
        vehicles = self._vehicles_module()
        try:
            descriptor = vehicles.VehicleDescr(
                compactDescr=record['compDescr'])
            shells = list(vehicles.getDefaultAmmoForGun(descriptor.gun))
        except Exception:
            return False
        if not shells or len(shells) % 2:
            return False
        self.equip_shells(_int(record.get('id', 0)), shells)
        return True

    # ---- purchases and settings -----------------------------------------

    def buy_item(self, compact_descr, count=1):
        """Own more of one item.

        Offline balances are unlimited: the shop publishes every item at zero
        price, so deducting credits would always subtract nothing. Ownership is
        the only part of a purchase that has an observable effect here.
        """
        compact_descr = _int(compact_descr)
        if not compact_descr:
            raise GarageError('a purchase needs an item')
        count = max(1, _int(count))
        item_type = self._item_type(compact_descr)
        existing = self._snapshot.get('inventoryItems', {}).get(item_type, {})
        self._publish_owned(
            compact_descr, item_type,
            int(existing.get(compact_descr, 0)) + count)
        self._price(compact_descr)
        self.revision += 1
        return compact_descr

    def _item_type(self, compact_descr):
        vehicles = self._vehicles_module()
        resolver = getattr(vehicles, 'getTypeOfCompactDescr', None)
        if resolver is None:
            from items import getTypeOfCompactDescr as resolver
        try:
            return int(resolver(compact_descr))
        except Exception as error:
            raise GarageError('unknown item %d: %s' % (compact_descr, error))

    def buy_and_equip_item(self, vehicle_inventory_id, compact_descr,
                           slot_index=0):
        """Own one item and mount it on the vehicle in the same request."""
        compact_descr = _int(compact_descr)
        item_type = self._item_type(compact_descr)
        self.buy_item(compact_descr, 1)
        if item_type == OPTIONAL_DEVICE_ITEM_TYPE:
            return self.equip_optional_device(
                vehicle_inventory_id, compact_descr, slot_index)
        if item_type == EQUIPMENT_ITEM_TYPE:
            record = self._record(vehicle_inventory_id)
            slots = list(record.get('eqs') or [0] * EQUIPMENT_SLOT_COUNT)
            slots += [0] * (EQUIPMENT_SLOT_COUNT - len(slots))
            index = _int(slot_index)
            if not 0 <= index < EQUIPMENT_SLOT_COUNT:
                raise GarageError('a vehicle has three equipment slots')
            slots[index] = compact_descr
            return self.equip_equipments(vehicle_inventory_id, slots)
        # Every remaining owned type is a vehicle module.
        return self.install_component(
            vehicle_inventory_id, compact_descr, slot_index)

    def change_vehicle_setting(self, vehicle_inventory_id, setting, is_on):
        """Set or clear one bit of a vehicle's settings mask.

        ``setting`` is already a ``VEHICLE_SETTINGS_FLAG`` value: #1513's
        VehicleSettingsProcessor sends AUTO_REPAIR (2) itself, not its index.
        """
        record = self._record(vehicle_inventory_id)
        bit = max(0, _int(setting))
        current = 0
        try:
            current = int(record.get('settings', 0) or 0)
        except (TypeError, ValueError):
            current = 0
        record['settings'] = (current | bit) if _int(is_on) else (
            current & ~bit)
        self.revision += 1
        return record

    # ---- crew -----------------------------------------------------------

    def add_tankman_skill(self, tankman_inventory_id, skill_index):
        record, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        names = getattr(tankmen, 'SKILL_NAMES', ())
        try:
            skill_name = names[_int(skill_index)]
        except (IndexError, TypeError):
            raise GarageError('unknown crew skill index %r' % (skill_index,))
        try:
            descriptor = tankmen.TankmanDescr(record['tankmen'][tankman_id])
            descriptor.addSkill(skill_name)
            record['tankmen'][tankman_id] = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the crew skill: %s' % error)
        self.revision += 1
        return record

    def drop_tankman_skills(self, tankman_inventory_id):
        record, tankman_id = self._tankman_record(tankman_inventory_id)
        tankmen = self._tankmen_module()
        try:
            descriptor = tankmen.TankmanDescr(record['tankmen'][tankman_id])
            descriptor.dropSkills(1.0, False)
            record['tankmen'][tankman_id] = descriptor.makeCompactDescr()
        except Exception as error:
            raise GarageError('the client refused the skill reset: %s' % error)
        self.revision += 1
        return record
