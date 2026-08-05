"""Small #1513 account snapshots consumed by the native account helpers.

The numeric item indices come from the target client's exact
``scripts/common/items/__init__.pyc``.  Keep this module engine-free so the
wire shapes can be tested without importing BigWorld.
"""


VEHICLE_ITEM_TYPE = 1
TANKMAN_ITEM_TYPE = 8
CUSTOMIZATION_ITEM_TYPE = 12
ITEM_TYPE_INDICES = tuple(range(1, 13))
REQUIRED_VEHICLE_COMPONENT_TYPES = (2, 3, 4, 5, 6, 7)


def _validate_selected_vehicle(vehicle):
    """Reject incomplete snapshots before native requesters consume them."""
    compact = vehicle.get('compDescr')
    if not compact:
        return

    crew = list(vehicle.get('crew', ()))
    tankmen = dict(vehicle.get('tankmen', {}))
    if not crew or not tankmen:
        raise ValueError('selected vehicle crew and tankmen must be non-empty')
    if any(tankman_id in (None, 0) for tankman_id in crew):
        raise ValueError('selected vehicle crew ids must be positive')
    if len(crew) != len(tankmen) or set(crew) != set(tankmen):
        raise ValueError('selected vehicle crew ids must resolve to tankmen')

    for key in ('repair', 'lock'):
        value = vehicle.get(key)
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ValueError('selected vehicle %s must contain two values' % key)
    if vehicle['repair'][1] <= 0:
        raise ValueError('selected vehicle health must be positive')
    for key in ('eqs', 'eqsLayout'):
        value = vehicle.get(key)
        if not isinstance(value, (tuple, list)) or len(value) != 3:
            raise ValueError('selected vehicle %s must contain three slots' % key)

    shells = vehicle.get('shells')
    if (not isinstance(shells, (tuple, list)) or not shells or
            len(shells) % 2):
        raise ValueError(
            'selected vehicle shells must contain descriptor/count pairs')
    if not isinstance(vehicle.get('shellsLayout'), dict):
        raise ValueError('selected vehicle shellsLayout must be a mapping')

    inventory_items = dict(vehicle.get('inventoryItems', {}))
    for item_type in REQUIRED_VEHICLE_COMPONENT_TYPES + (10,):
        items = inventory_items.get(item_type)
        if not isinstance(items, dict) or not items:
            raise ValueError(
                'selected vehicle item type %d must be non-empty' % item_type)

    item_prices = dict(vehicle.get('shopItemPrices', {}))
    for compact_descr, price in item_prices.items():
        if isinstance(price, dict):
            currencies = set(price)
            if (not currencies or
                    not currencies.issubset(
                        set(('credits', 'gold', 'crystal')))):
                raise ValueError(
                    'shop price %r must contain valid currencies' %
                    compact_descr)
        elif not isinstance(price, tuple) or len(price) < 2:
            raise ValueError(
                'shop price %r must be a currency mapping or tuple' %
                compact_descr)
    priced_items = set(item_prices)
    required_prices = set()
    for item_type in REQUIRED_VEHICLE_COMPONENT_TYPES + (10,):
        required_prices.update(inventory_items[item_type])
    if not required_prices.issubset(priced_items):
        raise ValueError(
            'selected vehicle modules and shells must have shop prices')
    shell_pairs = dict(
        (shells[index], shells[index + 1])
        for index in range(0, len(shells), 2))
    if shell_pairs != inventory_items[10]:
        raise ValueError(
            'selected vehicle shell layout and inventory must match')
    if int(vehicle.get('shopNationCount', 0)) <= 0:
        raise ValueError('selected vehicle shop nation count must be positive')
    if int(vehicle.get('customizationItemCount', 0)) <= 0:
        raise ValueError(
            'selected vehicle customization catalogue must be non-empty')


def inventory(selected_vehicle=None):
    """Return the selected vehicle and its relational inventory records.

    ``selected_vehicle`` is serialized by ``bootstrap._selected_vehicle``.  It
    carries engine-derived compact descriptors, while this low-level module
    stays importable without BigWorld or the item definition cache.
    """
    vehicle = selected_vehicle if isinstance(selected_vehicle, dict) else {}
    _validate_selected_vehicle(vehicle)
    compact = vehicle.get('compDescr')
    values = dict((item_type, {}) for item_type in ITEM_TYPE_INDICES)
    vehicle_values = {
        'repair': {}, 'lastCrew': {}, 'crew': {}, 'settings': {},
        'compDescr': {}, 'eqs': {}, 'eqsLayout': {}, 'shells': {},
        'shellsLayout': {}, 'lock': {},
    }
    if compact:
        vehicle_id = int(vehicle.get('id', 1))
        crew = list(vehicle.get('crew', ()))
        tankmen = dict(vehicle.get('tankmen', {}))
        vehicle_values['crew'][vehicle_id] = crew
        # #1513's InventoryRequester defaults a missing repair entry to the
        # integer 0, while the GUI Vehicle constructor unpacks a two-tuple.
        vehicle_values['repair'][vehicle_id] = tuple(
            vehicle.get('repair', (0, 0)))
        vehicle_values['settings'][vehicle_id] = 0
        vehicle_values['compDescr'][vehicle_id] = compact
        vehicle_values['eqs'][vehicle_id] = list(
            vehicle.get('eqs', (0, 0, 0)))
        vehicle_values['eqsLayout'][vehicle_id] = list(
            vehicle.get('eqsLayout', (0, 0, 0)))
        vehicle_values['shells'][vehicle_id] = list(
            vehicle.get('shells', ()))
        # GUI Vehicle calls .get() on this value before parsing the layout.
        vehicle_values['shellsLayout'][vehicle_id] = dict(
            vehicle.get('shellsLayout', {}))
        # GUI Vehicle.isLocked indexes both positions without normalizing the
        # InventoryRequester default (which is the integer zero).
        vehicle_values['lock'][vehicle_id] = tuple(
            vehicle.get('lock', (0, 0)))
        # A missing lastCrew record means that no historical crew is stored.
        # An empty per-vehicle list is not equivalent in #1513: the crew
        # operations popover treats presence as a real history entry.

        for item_type, items in dict(
                vehicle.get('inventoryItems', {})).items():
            item_type = int(item_type)
            if item_type in values and item_type not in (
                    VEHICLE_ITEM_TYPE, TANKMAN_ITEM_TYPE,
                    CUSTOMIZATION_ITEM_TYPE):
                values[item_type] = dict(items)

        values[TANKMAN_ITEM_TYPE] = {
            'compDescr': tankmen,
            # This foreign key is the vehicle inventory id, not its type id.
            'vehicle': dict((tankman_id, vehicle_id)
                            for tankman_id in tankmen),
        }
    values[VEHICLE_ITEM_TYPE] = vehicle_values
    if not compact:
        values[TANKMAN_ITEM_TYPE] = {'vehicle': {}, 'compDescr': {}}
    values[CUSTOMIZATION_ITEM_TYPE] = {}
    return {'inventory': values}


def stats():
    """Return conservative, zeroed stats suitable for a local selected vehicle."""
    return {
        'account': {
            'clanDBID': 0, 'attrs': 0, 'premiumExpiryTime': 0,
            'autoBanTime': 0, 'globalRating': 0,
        },
        'stats': {
            'credits': 0, 'gold': 0, 'crystal': 0, 'freeXP': 0,
            'slots': 1, 'berths': 0, 'accOnline': 0, 'accOffline': 0,
            'freeTMenLeft': 0, 'freeVehiclesLeft': 0,
            'vehicleSellsLeft': 0, 'captchaTriesLeft': 0,
            'denunciationsLeft': 0, 'tutorialsCompleted': 0,
            'battlesTillCaptcha': 0, 'dailyPlayHours': [0],
            # Full daily/weekly periods disable parental-control blocking in
            # the native #1513 GameSessionController.  Zero means no allowed
            # play time, not "unlimited".
            'playLimits': ((86400, ''), (604800, '')), 'vehTypeXP': {},
            'vehTypeLocks': {}, 'restrictions': {},
            'globalVehicleLocks': {}, 'refSystem': {'referrals': {}},
            'unlocks': set(), 'eliteVehicles': set(),
            'multipliedXPVehs': set(),
        },
        'cache': {
            'isFinPswdVerified': True,
            'mayConsumeWalletResources': False,
            'unitAcceptDeadline': 0,
            'oldVehInvIDs': set(),
        },
    }


def sync_data(revision=0, selected_vehicle=None, int_user_settings=None):
    # These are deliberately present even when empty.  #1513's account
    # helpers only create a requester cache entry when the corresponding key
    # exists in the sync diff; several lobby requesters then index that entry
    # directly instead of applying a missing-value default.
    result = {
        'rev': int(revision) + 1,
        'prevRev': int(revision),
        'quests': {},
        'tokens': {},
        'potapovQuests': {
            'compDescr': '',
            'regular': {'slots': 0, 'selected': [], 'lastIDs': {}},
            'training': {'slots': 0, 'selected': [], 'lastIDs': {}},
        },
        'intUserSettings': dict(int_user_settings or {}),
        'goodies': {},
        'groupLocks': {'groupBattles': [], 'isGroupLocked': []},
        'vehiclesGroupMapping': {},
        'recycleBin': {},
        'ranked': {},
        'badges': (),
        'newYear': {},
        'eventsData': {},
    }
    result.update(inventory(selected_vehicle))
    result.update(stats())
    return result


def shop(revision=0, selected_vehicle=None):
    """Return the smallest stream accepted by #1513 ``Shop``.

    The lobby/map picker does not buy items, but the native account lifecycle
    always synchronizes the shop.  ``sellPriceFactor`` is mandatory in
    ``Shop.__onSyncDataReceived``; the remaining values keep read-only getters
    deterministic instead of leaving a half-synchronized cache.
    """
    vehicle = selected_vehicle if isinstance(selected_vehicle, dict) else {}
    _validate_selected_vehicle(vehicle)
    item_prices = dict(vehicle.get('shopItemPrices', {}))
    nation_count = max(1, int(vehicle.get('shopNationCount', 16)))
    empty_items = {
        'itemPrices': item_prices,
        'notInShopItems': set(),
        'vehiclesNotToBuy': set(),
        'vehiclesRentPrices': {},
        'vehiclesToSellForGold': set(),
        'vehicleSellPriceFactors': {},
        # Legacy and customization 2.0 requesters both index these arrays by
        # nation without guarding an empty default.  The exact count comes
        # from nations.NAMES in bootstrap; the values remain read-only.
        'inscriptionGroupPriceFactors': [
            {} for unused_index in range(nation_count)],
        'notInShopInscriptionGroups': [
            set() for unused_index in range(nation_count)],
        'camouflagePriceFactors': [
            {} for unused_index in range(nation_count)],
        'notInShopCamouflages': [
            set() for unused_index in range(nation_count)],
        'playerEmblemGroupPriceFactors': {},
        'notInShopPlayerEmblemGroups': set(),
        'vehicleCamouflagePriceFactors': {},
        'vehicleHornPriceFactors': {},
    }
    empty_goodies = {'prices': {}, 'notInShop': set(), 'goodies': {}}
    return {
        'rev': int(revision) + 1,
        'prevRev': int(revision),
        'crystalExchangeRate': 0,
        'sellPriceFactor': 0.5,
        'items': dict(empty_items),
        'defaults': {
            'items': dict(empty_items),
            'freeXPToTManXPRate': 10,
            'goodies': dict(empty_goodies),
            'paidRemovalCost': {'gold': 0},
        },
        'goodies': dict(empty_goodies),
        # Exact #1513 consumers fall back to the final price entry and the
        # berth helper divides by pack size.  Empty lists and a zero pack size
        # therefore crash even though the outer tuple arity is correct.
        'berthsPrices': (0, 1, [0]),
        'slotsPrices': (0, [0]),
        # Stock-compatible, non-zero exchange ratios.  The native exchange
        # dialogs divide by both freeXPConversion[0] and this tankman rate.
        'freeXPConversion': (25, 1),
        'dropSkillsCost': {
            0: {
                'credits': 0, 'gold': 0, 'xpReuseFraction': 0.5,
            },
            1: {
                'credits': 0, 'gold': 0, 'xpReuseFraction': 0.5,
            },
            2: {
                'credits': 0, 'gold': 0, 'xpReuseFraction': 1.0,
            },
        },
        # The three native recruitment choices are positional.  Keep their
        # complete descriptor dictionaries even though offline prices are 0.
        'tankmanCost': (
            {
                'credits': 0, 'gold': 0, 'roleLevel': 50,
                'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
                'isPremium': False,
            },
            {
                'credits': 0, 'gold': 0, 'roleLevel': 75,
                'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
                'isPremium': False,
            },
            {
                'credits': 0, 'gold': 0, 'roleLevel': 100,
                'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
                'isPremium': True,
            },
        ),
        'premiumCost': {},
        # RefSystem.__update indexes posByXPinTeam directly.  Once this dict is
        # non-empty, its #1513 helpers also index the other three values, so
        # keep the entire native disabled/default shape together.
        'refSystem': {
            'periods': 0,
            'maxReferralXPPool': 0,
            'maxNumberOfReferrals': 0,
            'posByXPinTeam': 0,
        },
        # ShopRequester calls .get(Currency.GOLD) on this value.
        'paidRemovalCost': {'gold': 0},
        'dailyXPFactor': 1,
        'changeRoleCost': 0,
        'freeXPToTManXPRate': 10,
        'exchangeRate': 0,
        'exchangeRateForShellsAndEqs': 0,
        'isEnabledBuyingGoldShellsForCredits': False,
        'isEnabledBuyingGoldEqsForCredits': False,
    }


def dossiers(revision=0):
    """Return the exact tuple unpacked by #1513 ``DossierCache``."""
    return (int(revision) + 1, [])
