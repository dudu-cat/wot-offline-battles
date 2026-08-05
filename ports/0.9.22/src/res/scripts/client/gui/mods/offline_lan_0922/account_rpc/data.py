"""Small #1513 account snapshots consumed by the native account helpers.

The numeric item indices come from the target client's exact
``scripts/common/items/__init__.pyc``.  Keep this module engine-free so the
wire shapes can be tested without importing BigWorld.
"""


VEHICLE_ITEM_TYPE = 1
TANKMAN_ITEM_TYPE = 8
CUSTOMIZATION_ITEM_TYPE = 12
ITEM_TYPE_INDICES = tuple(range(1, 13))


def inventory(selected_vehicle=None):
    """Return the minimum sync-data inventory shape used by selected vehicle UI.

    ``selected_vehicle`` is a serialized mapping with ``id`` and ``compDescr``;
    no items/vehicles import is performed in this low-level module.
    """
    vehicle = selected_vehicle if isinstance(selected_vehicle, dict) else {}
    compact = vehicle.get('compDescr')
    values = dict((item_type, {}) for item_type in ITEM_TYPE_INDICES)
    vehicle_values = {
        'repair': {}, 'lastCrew': {}, 'crew': {}, 'settings': {},
        'compDescr': {}, 'eqs': {}, 'eqsLayout': {}, 'shells': {},
        'shellsLayout': {}, 'lock': {},
    }
    if compact:
        vehicle_id = int(vehicle.get('id', 1))
        vehicle_values['crew'][vehicle_id] = []
        # #1513's InventoryRequester defaults a missing repair entry to the
        # integer 0, while the GUI Vehicle constructor unpacks a two-tuple.
        vehicle_values['repair'][vehicle_id] = (0, 0)
        vehicle_values['settings'][vehicle_id] = 0
        vehicle_values['compDescr'][vehicle_id] = compact
        vehicle_values['eqs'][vehicle_id] = []
        vehicle_values['eqsLayout'][vehicle_id] = []
        vehicle_values['shells'][vehicle_id] = []
        # GUI Vehicle calls .get() on this value before parsing the layout.
        vehicle_values['shellsLayout'][vehicle_id] = {}
    values[VEHICLE_ITEM_TYPE] = vehicle_values
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


def shop(revision=0):
    """Return the smallest stream accepted by #1513 ``Shop``.

    The lobby/map picker does not buy items, but the native account lifecycle
    always synchronizes the shop.  ``sellPriceFactor`` is mandatory in
    ``Shop.__onSyncDataReceived``; the remaining values keep read-only getters
    deterministic instead of leaving a half-synchronized cache.
    """
    empty_items = {
        'itemPrices': {},
        'notInShopItems': set(),
        'vehiclesNotToBuy': set(),
        'vehiclesRentPrices': {},
        'vehiclesToSellForGold': set(),
        'vehicleSellPriceFactors': {},
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
            'freeXPToTManXPRate': 0,
            'goodies': dict(empty_goodies),
            'paidRemovalCost': {'gold': 0},
        },
        'goodies': dict(empty_goodies),
        'berthsPrices': (0, 0, []),
        'slotsPrices': (0, []),
        'freeXPConversion': (0, 0),
        'dropSkillsCost': {},
        'tankmanCost': (),
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
        'freeXPToTManXPRate': 0,
        'exchangeRate': 0,
        'exchangeRateForShellsAndEqs': 0,
        'isEnabledBuyingGoldShellsForCredits': False,
        'isEnabledBuyingGoldEqsForCredits': False,
    }


def dossiers(revision=0):
    """Return the exact tuple unpacked by #1513 ``DossierCache``."""
    return (int(revision) + 1, [])
