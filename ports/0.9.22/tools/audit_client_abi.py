#!/usr/bin/env python2
"""Check the pinned #1513 Python ABI without importing BigWorld modules.

Function signatures are read directly from CPython 2.7 code objects in the
client's ``scripts.pkg``.  Data shapes live in the producer contract tests;
signatures alone cannot describe dictionary keys or tuple payload lengths.
"""

from __future__ import print_function

import argparse
import json
import marshal
import opcode
import os
import sys
import types
import zipfile


EXPECTED_ABI = {
    'scripts/client/Account.pyc': {
        'PlayerAccount.__init__': ('self',),
        'PlayerAccount.onBecomePlayer': ('self',),
        'PlayerAccount.onBecomeNonPlayer': ('self',),
        'PlayerAccount.onCmdResponse': (
            'self', 'requestID', 'resultID', 'errorStr'),
        'PlayerAccount.onCmdResponseExt': (
            'self', 'requestID', 'resultID', 'errorStr', 'ext'),
        'PlayerAccount.onStreamComplete': ('self', 'id', 'desc', 'data'),
        'PlayerAccount.showGUI': ('self', 'ctx'),
        'PlayerAccount.receiveServerStats': ('self', 'stats'),
        'PlayerAccount._update': ('self', 'triggerEvents', 'diff'),
    },
    'scripts/client_common/ClientChat.pyc': {
        'ClientChat.__init__': ('self',),
        'ClientChat.onChatAction': ('self', 'chatActionData'),
        'ClientChat.unsubscribeChatAction': (
            'self', 'callback', 'action', 'channelId'),
        'ClientChat.__dataTimeProcessor': ('self', 'actionData'),
    },
    'scripts/client/ChatManager.pyc': {
        'ChatManager.switchPlayerProxy': ('self', 'proxy'),
    },
    'scripts/client/account_helpers/AccountSyncData.pyc': {
        'AccountSyncData.setAccount': ('self', 'account'),
    },
    'scripts/client/account_helpers/persistent_caches.pyc': {
        'SimpleCache.setAccount': ('self', 'account'),
        'SimpleCache.getFileName': ('self',),
        'cacheFileName': ('account', 'cacheType', 'cacheName'),
    },
    'scripts/client/account_helpers/QuestProgress.pyc': {
        'QuestProgress.synchronize': ('self', 'isFullSync', 'diff'),
    },
    'scripts/client/account_helpers/AccountValidator.pyc': {
        'AccountValidator.validate': ('self', 'callback'),
    },
    'scripts/client/account_helpers/Shop.pyc': {
        'Shop.__onSyncComplete': ('self', 'syncID', 'data'),
        'Shop.__onSyncDataReceived': ('self', 'data'),
    },
    'scripts/client/account_helpers/DossierCache.pyc': {
        'DossierCache.__onSyncComplete': ('self', 'syncID', 'data'),
    },
    'scripts/client/gui/shared/utils/requesters/QuestsProgressRequester.pyc': {
        '_PersonalMissionsProgressRequester._response': (
            'self', 'resID', 'value', 'callback'),
    },
    'scripts/common/items/tankmen.pyc': {
        'generateTankmen': (
            'nationID', 'vehicleTypeID', 'roles', 'isPremium',
            'roleLevel', 'skillsMask', 'isPreview'),
        'TankmanDescr.__init__': ('self', 'compactDescr', 'battleOnly'),
    },
    'scripts/common/items/__init__.pyc': {
        'ItemsPrices.__init__': ('self', 'prices'),
        'ItemsPrices.getPrices': ('self', 'descriptor'),
        'makeIntCompactDescrByID': (
            'itemTypeName', 'nationID', 'itemID'),
    },
    'scripts/common/items/vehicles.pyc': {
        'VehicleDescr': ('compactDescr', 'typeID', 'typeName'),
        'getDefaultAmmoForGun': ('gunDescr',),
        'Cache.customization20': ('self',),
        'VehicleList.getList': ('self', 'nationID'),
    },
    'scripts/common/items/VehicleDescrCrew.pyc': {
        'VehicleDescrCrew.__init__': (
            'self', 'vehicleDescr', 'crewCompactDescrs',
            'mainSkillQualifiersApplier', 'activityFlags', 'isFire',
            'stunFactors'),
        'VehicleDescrCrew._validateAndComputeCrew': ('self',),
    },
    'scripts/common/items/item_price.pyc': {
        'getNextSlotPrice': ('slots', 'slotsPrices'),
        'getNextBerthPackPrice': ('berths', 'berthsPrices'),
    },
    'scripts/client/gui/shared/items_parameters/functions.pyc': {
        'extractCrewDescrs': ('vehicle', 'replaceNone'),
    },
    'scripts/client/gui/shared/utils/requesters/ItemsRequester.pyc': {
        'ItemsRequester.getItemsEx': (
            'self', 'itemTypeIDs', 'criteria', 'nationID'),
    },
    'scripts/client/gui/shared/utils/requesters/parsers/ShopDataParser.pyc': {
        'ShopDataParser.getItemsIterator': (
            'self', 'nationID', 'itemTypeID'),
    },
    'scripts/client/gui/shared/gui_items/Vehicle.pyc': {
        'Vehicle.descriptor': ('self',),
        'Vehicle._calcCrewBonuses': ('self', 'crew', 'proxy'),
        'Vehicle._buildCrew': ('self', 'crew', 'proxy'),
        'Vehicle._parseShells': (
            'self', 'layoutList', 'defaultLayoutList', 'proxy'),
        'Vehicle.isLocked': ('self',),
        'Vehicle.typeOfLockingArena': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/hangar/'
    'AmmunitionPanel.pyc': {
        'getFittingSlotsData': (
            'vehicle', 'slotsRange', 'VoClass', 'itemsCache'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/shared/'
    'fitting_slot_vo.pyc': {
        'FittingSlotVO._prepareModule': (
            'self', 'modulesData', 'vehicle', 'slotType', 'slotId'),
        'HangarFittingSlotVO._prepareModule': (
            'self', 'modulesData', 'vehicle', 'slotType', 'slotId'),
    },
    'scripts/client/Avatar.pyc': {
        'PlayerAvatar.__init__': ('self',),
        'PlayerAvatar.onBecomePlayer': ('self',),
        'PlayerAvatar.onBecomeNonPlayer': ('self',),
        'PlayerAvatar.onEnterWorld': ('self', 'prereqs'),
        'PlayerAvatar.onLeaveWorld': ('self',),
        'PlayerAvatar.onPrereqsLoaded': (
            'self', 'resNames', 'resourceRefs'),
        'PlayerAvatar.leaveArena': ('self',),
        'PlayerAvatar.onVehicleChanged': ('self',),
        'PlayerAvatar.onCmdResponse': (
            'self', 'requestID', 'resultID', 'errorStr'),
        'PlayerAvatar.onTokenReceived': (
            'self', 'requestID', 'tokenType', 'data'),
        'PlayerAvatar.receiveAccountStats': (
            'self', 'requestID', 'stats'),
        'PlayerAvatar.prerequisites': ('self',),
        'PlayerAvatar.set_playerVehicleID': ('self', 'prev'),
        'PlayerAvatar.__onSetOwnVehicleAuxPhysicsData': ('self', 'prev'),
        'PlayerAvatar.__onInitStepCompleted': ('self',),
        'PlayerAvatar.vehicle_onEnterWorld': ('self', 'vehicle'),
        'PlayerAvatar.updateVehicleHealth': (
            'self', 'vehicleID', 'health', 'deathReasonID', 'isCrewActive',
            'isRespawn'),
        'PlayerAvatar.updateVehicleGunReloadTime': (
            'self', 'vehicleID', 'timeLeft', 'baseTime'),
        'PlayerAvatar.updateVehicleAmmo': (
            'self', 'vehicleID', 'compactDescr', 'quantity',
            'quantityInClip', 'timeRemaining'),
        'PlayerAvatar.updateVehicleSetting': (
            'self', 'vehicleID', 'code', 'value'),
        'PlayerAvatar.updateOwnVehiclePosition': (
            'self', 'position', 'direction', 'speed', 'rspeed'),
        'PlayerAvatar.updateTargetingInfo': (
            'self', 'turretYaw', 'gunPitch', 'maxTurretRotationSpeed',
            'maxGunRotationSpeed', 'shotDispMultiplierFactor',
            'gunShotDispersionFactorsTurretRotation',
            'chassisShotDispersionFactorsMovement',
            'chassisShotDispersionFactorsRotation', 'aimingTime'),
        'PlayerAvatar.syncVehicleAttrs': ('self', 'attrs'),
        'PlayerAvatar.updateArena': ('self', 'updateType', 'argStr'),
        'PlayerAvatar.onRoundFinished': ('self', 'winnerTeam', 'reason'),
    },
    'scripts/client/Vehicle.pyc': {
        'Vehicle.__init__': ('self',),
        'Vehicle.__startWGPhysics': ('self',),
        'Vehicle.prerequisites': ('self', 'respawnCompactDescr'),
        'Vehicle.onEnterWorld': ('self', 'prereqs'),
        'Vehicle.onLeaveWorld': ('self',),
        'Vehicle.showShooting': ('self', 'burstCount', 'isPredictedShot'),
        'Vehicle.set_health': ('self', 'prev'),
        'Vehicle.set_isCrewActive': ('self', 'prev'),
        'Vehicle.set_gunAnglesPacked': ('self', 'prev'),
        'Vehicle.getAimParams': ('self',),
    },
    'scripts/client/vehicle_systems/CompoundAppearance.pyc': {
        'CompoundAppearance.__onModelsRefresh': (
            'self', 'modelState', 'resourceList'),
    },
    'scripts/client/OfflineMapCreator.pyc': {
        'OfflineMapCreator.create': ('self', 'mapName'),
        'OfflineMapCreator.destroy': ('self',),
        'OfflineMapCreator.cancel': ('self',),
        'OfflineMapCreator.Active': ('self',),
        'OfflineMapCreator.SetActive': ('self', '_active'),
        'OfflineMapCreator.__setupCamera': ('self',),
    },
    'scripts/client/connection_mgr.pyc': {
        'ConnectionManager.initiateConnection': (
            'self', 'params', 'password', 'serverName'),
        'ConnectionManager.disconnect': ('self',),
    },
    'scripts/client/gui/app_loader/loader.pyc': {
        '_AppLoader.getDefLobbyApp': ('self',),
        '_AppLoader.getSpaceID': ('self',),
        '_AppLoader.showBattleLoading': ('self',),
        '_AppLoader.showBattlePage': ('self',),
        '_AppLoader.showLobby': ('self',),
    },
    'scripts/client/gui/app_loader/states.pyc': {
        'LobbyState.getSpaceID': ('self',),
        'LobbyState._getNextState': ('self', 'ctx'),
        'BattleLoadingState.getSpaceID': ('self',),
        'BattleLoadingState._getNextState': ('self', 'ctx'),
        'BattleLoadingState._createBattleState': ('self',),
        'BattleState.getSpaceID': ('self',),
        'BattleState._getNextState': ('self', 'ctx'),
        'LoginState.init': ('self', 'ctx'),
        'LoginState.update': ('self', 'ctx'),
        'LoginState._clearEntitiesAndSpaces': (),
    },
    'scripts/client/gui/battle_control/controllers/arena_load_ctrl.pyc': {
        'ArenaLoadController.invalidateArenaInfo': ('self',),
        'ArenaLoadController.arenaLoadCompleted': ('self',),
    },
    'scripts/client/gui/battle_control/controllers/repositories.pyc': {
        'SharedControllersLocator.arenaLoad': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/LobbyView.pyc': {
        'LobbyView._populate': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/header/'
    'LobbyHeader.pyc': {
        'LobbyHeader.fightClick': ('self', 'mapID', 'actionName'),
        'LobbyHeader._updatePrebattleControls': ('self',),
        'LobbyHeader._checkFightButtonDisabled': (
            'self', 'canDo', 'isFightButtonForcedDisabled'),
        'LobbyHeader.__handleFightButtonUpdated': ('self', '_'),
        'LobbyHeader.__addListeners': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/meta/LobbyHeaderMeta.pyc': {
        'LobbyHeaderMeta.as_disableFightButtonS': (
            'self', 'isDisabled'),
    },
    'scripts/client/gui/Scaleform/framework/entities/'
    'BaseDAAPIModule.pyc': {
        'BaseDAAPIModule.setFlashObject': (
            'self', 'movieClip', 'autoPopulate', 'setScript'),
    },
    'scripts/client/gui/Scaleform/framework/entities/DAAPIEntity.pyc': {
        'DAAPIEntity.turnDAAPIon': ('self', 'setScript', 'movieClip'),
    },
    'scripts/client/gui/prb_control/events_dispatcher.pyc': {
        'EventDispatcher.updateUI': ('self', 'loadedAlias'),
    },
    'scripts/client/gui/shared/event_bus.pyc': {
        'EventBus.handleEvent': ('self', 'event', 'scope'),
    },
    'scripts/client/gui/Scaleform/framework/application.pyc': {
        'SFApplication.loadView': (
            'self', 'loadParams', '*args', '**kwargs'),
    },
    'scripts/client/gui/Scaleform/framework/managers/loaders.pyc': {
        'ViewLoadParams.__init__': (
            'self', 'alias', 'name', 'loadMode'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/trainings/'
    'TrainingSettingsWindow.pyc': {
        'TrainingSettingsWindow.__init__': ('self', 'ctx'),
        'TrainingSettingsWindow.getMapsData': ('self',),
        'TrainingSettingsWindow.getInfo': ('self',),
        'TrainingSettingsWindow.onWindowClose': ('self',),
        'TrainingSettingsWindow.updateTrainingRoom': (
            'self', 'arena', 'roundLength', 'isPrivate', 'comment'),
    },
    'scripts/client/gui/Scaleform/daapi/view/meta/TrainingWindowMeta.pyc': {
        'TrainingWindowMeta.as_setDataS': ('self', 'info', 'mapsData'),
    },
    'scripts/client_common/ClientArena.pyc': {
        'ClientArena.update': ('self', 'updateType', 'argStr'),
        'ClientArena.__onVehicleListUpdate': ('self', 'argStr'),
        'ClientArena.__onVehicleAddedUpdate': ('self', 'argStr'),
        'ClientArena.__onPeriodInfoUpdate': ('self', 'argStr'),
        'ClientArena.__onVehicleKilled': ('self', 'argStr'),
        'ClientArena.__onAvatarReady': ('self', 'argStr'),
    },
    'scripts/client/gui/game_control/RefSystem.pyc': {
        '_getRefSysCfg': ('itemsCache',),
        'RefSystem.__update': ('self', 'data'),
    },
    'scripts/client/gui/game_control/state_tracker.pyc': {
        'GameStateTracker.init': ('self',),
        'GameStateTracker.fini': ('self',),
        'GameStateTracker.onAccountShowGUI': ('self', 'ctx'),
        'GameStateTracker.onLobbyInited': ('self', 'event'),
        'GameStateTracker.onLobbyStarted': ('self', 'ctx'),
        'GameStateTracker._invoke': ('self', 'method', '*args'),
    },
    'scripts/client/gui/shared/personality.pyc': {
        'onAccountShowGUI': ('ctx',),
        'onCenterIsLongDisconnected': ('isLongDisconnected',),
    },
    'scripts/client/gui/shared/utils/HangarSpace.pyc': {
        '_HangarSpace.inited': ('self',),
        '_HangarSpace.spaceInited': ('self',),
        '_HangarSpace.getVehicleEntity': ('self',),
    },
    'scripts/client/CurrentVehicle.pyc': {
        '_CachedVehicle.isPresent': ('self',),
        '_CurrentVehicle.item': ('self',),
    },
    'scripts/client/gui/ClientHangarSpace.pyc': {
        'ClientHangarSpace.getVehicleEntity': ('self',),
        '_VehicleAppearance.__startBuild': ('self', 'vDesc', 'vState'),
        '_VehicleAppearance.__onResourcesLoaded': (
            'self', 'buildInd', 'resourceRefs'),
        '_VehicleAppearance.__doFinalSetup': ('self', 'buildIdx', 'model'),
    },
    'scripts/client/gui/prb_control/dispatcher.pyc': {
        '_PrbControlLoader.createBattleDispatcher': ('self',),
        '_PrbControlLoader.getDispatcher': ('self',),
    },
    'scripts/client/gui/prb_control/__init__.pyc': {
        'prbDispatcherProperty.__get__': ('self', 'obj', 'objType'),
    },
    'scripts/client/SoundGroups.pyc': {
        'SoundGroups.destroy': ('self',),
    },
    'scripts/client/helpers/server_settings.pyc': {
        'ServerSettings.isElenEnabled': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/login/EULADispatcher.pyc': {
        'EULADispatcher.processLicense': ('self', 'callback'),
        'EULADispatcher.__saveVersionFile': ('self',),
    },
}


# Signatures cannot describe dictionary payloads or dynamic getattr targets.
# These literals are direct string subscripts or native-method names in exact
# #1513 consumers. Producer contract tests verify dictionary payload shapes.
EXPECTED_CODE_LITERALS = {
    'scripts/client/Avatar.pyc': {
        'PlayerAvatar.__onSetOwnVehicleAuxPhysicsData': (
            'syncStabilisedYPR',),
    },
    'scripts/client/Vehicle.pyc': {
        'Vehicle.set_gunAnglesPacked': ('syncGunAngles',),
    },
    'scripts/client_common/ClientChat.pyc': {
        'ClientChat.__dataTimeProcessor': ('time', 'sentTime'),
    },
    'scripts/client/account_helpers/QuestProgress.pyc': {
        'QuestProgress.synchronize': ('quests', 'tokens', 'potapovQuests'),
    },
    'scripts/client/account_helpers/Shop.pyc': {
        'Shop.__onSyncDataReceived': ('sellPriceFactor',),
    },
    'scripts/client/gui/shared/utils/requesters/QuestsProgressRequester.pyc': {
        '_PersonalMissionsProgressRequester._response': (
            'potapovQuests', 'compDescr'),
    },
    'scripts/client/gui/game_control/RefSystem.pyc': {
        '_getRefSysCfg': (
            'periods', 'maxReferralXPPool', 'maxNumberOfReferrals'),
        'RefSystem.__update': ('posByXPinTeam',),
    },
    'scripts/client/gui/game_control/state_tracker.pyc': {
        'GameStateTracker.onLobbyStarted': ('onLobbyStarted',),
    },
    'scripts/client/gui/shared/personality.pyc': {
        'onAccountShowGUI': ('rareAchievements',),
    },
    'scripts/client/gui/Scaleform/daapi/view/login/EULADispatcher.pyc': {
        'EULADispatcher.processLicense': ('version',),
        'EULADispatcher.__saveVersionFile': ('version',),
    },
    'scripts/client/helpers/server_settings.pyc': {
        'ServerSettings.isElenEnabled': ('elenSettings', 'isElenEnabled'),
    },
}


# These global/attribute names capture lifecycle semantics that signatures and
# string payload literals cannot express.  They are the exact #1513 APIs the
# offline Account preservation and native lobby-ready gate depend on.
EXPECTED_CODE_NAMES = {
    'scripts/client/Account.pyc': {
        'PlayerAccount.onBecomePlayer': ('BigWorld', 'clearAllSpaces'),
        'PlayerAccount.onBecomeNonPlayer': (
            'chatManager', 'switchPlayerProxy', 'events',
            'onAccountBecomeNonPlayer'),
    },
    'scripts/client/Avatar.pyc': {
        'PlayerAvatar.__init__': (
            'Account', 'g_accountRepository', 'intUserSettings',
            'prebattleInvitations'),
        'PlayerAvatar.onBecomeNonPlayer': (
            'chatManager', 'switchPlayerProxy', 'g_playerEvents',
            'onAvatarBecomeNonPlayer'),
        'PlayerAvatar.set_playerVehicleID': (
            'BigWorld', 'entity', 'inWorld'),
        'PlayerAvatar.vehicle_onEnterWorld': (
            'playerVehicleID', 'VEHICLE_ENTERED',
            '_PlayerAvatar__onInitStepCompleted'),
        'PlayerAvatar.__onInitStepCompleted': ('setClientReady',),
    },
    'scripts/client/Vehicle.pyc': {
        'Vehicle.__startWGPhysics': ('filter', 'syncGunAngles', 'speedInfo'),
        'Vehicle.prerequisites': (
            'typeDescriptor', 'appearance_cache', 'createAppearance'),
        'Vehicle.onEnterWorld': (
            'vehicle_onEnterWorld', 'sendStateToOwnClient'),
    },
    'scripts/client/vehicle_systems/CompoundAppearance.pyc': {
        'CompoundAppearance.__onModelsRefresh': (
            '_CompoundAppearance__filter', 'syncGunAngles'),
    },
    'scripts/client_common/ClientChat.pyc': {
        'ClientChat.__init__': ('_ClientChat__chatActionCallbacks',),
    },
    'scripts/client/ChatManager.pyc': {
        'ChatManager.switchPlayerProxy': (
            '_ChatManager__cleanupMyCallbacks', 'playerProxy'),
    },
    'scripts/client/gui/shared/personality.pyc': {
        'onCenterIsLongDisconnected': (
            'BigWorld', 'player', 'isLongDisconnectedFromCenter'),
    },
    'scripts/client/gui/app_loader/states.pyc': {
        'LobbyState.getSpaceID': ('_SPACE_ID', 'LOBBY'),
        'LobbyState._getNextState': (
            '_SPACE_ID', 'BATTLE_LOADING', 'BattleLoadingState'),
        'BattleLoadingState.getSpaceID': (
            '_SPACE_ID', 'BATTLE_LOADING'),
        'BattleLoadingState._getNextState': (
            '_SPACE_ID', 'BATTLE', '_doStartBattle',
            '_createBattleState', 'LOBBY', 'LobbyState'),
        'BattleLoadingState._createBattleState': ('BattleState',),
        'BattleState.getSpaceID': ('_SPACE_ID', 'BATTLE'),
        'BattleState._getNextState': (
            '_SPACE_ID', 'WAITING', 'WaitingState'),
        'LoginState.init': ('_clearEntitiesAndSpaces',),
        'LoginState.update': ('_clearEntitiesAndSpaces',),
        'LoginState._clearEntitiesAndSpaces': (
            'BigWorld', 'clearEntitiesAndSpaces'),
    },
    'scripts/client/gui/app_loader/loader.pyc': {
        '_AppLoader.showBattleLoading': (
            'changeSpace', '_SPACE_ID', 'BATTLE_LOADING'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/header/'
    'LobbyHeader.pyc': {
        'LobbyHeader.fightClick': (
            'lobbyContext', 'isHeaderNavigationPossible',
            'prbDispatcher', 'doAction', 'PrbAction'),
        'LobbyHeader._updatePrebattleControls': (
            'prbEntity', 'canPlayerDoAction',
            '_checkFightButtonDisabled', 'as_disableFightButtonS'),
        'LobbyHeader.__handleFightButtonUpdated': (
            '_updatePrebattleControls',),
        'LobbyHeader.__addListeners': (
            'FightButtonEvent', 'FIGHT_BUTTON_UPDATE',
            '_LobbyHeader__handleFightButtonUpdated',
            'EVENT_BUS_SCOPE', 'LOBBY'),
    },
    'scripts/client/gui/Scaleform/daapi/view/meta/LobbyHeaderMeta.pyc': {
        'LobbyHeaderMeta.as_disableFightButtonS': (
            '_isDAAPIInited', 'flashObject', 'as_disableFightButton'),
    },
    'scripts/client/gui/Scaleform/framework/entities/'
    'BaseDAAPIModule.pyc': {
        'BaseDAAPIModule.setFlashObject': (
            'turnDAAPIon', 'isCreated', 'create'),
    },
    'scripts/client/gui/Scaleform/framework/entities/DAAPIEntity.pyc': {
        'DAAPIEntity.turnDAAPIon': (
            '_DAAPIEntity__isDAAPIInited',
            '_DAAPIEntity__flashObject', 'script'),
    },
    'scripts/client/gui/prb_control/events_dispatcher.pyc': {
        'EventDispatcher.updateUI': (
            '_EventDispatcher__fireEvent', 'FightButtonEvent',
            'FIGHT_BUTTON_UPDATE', '_EventDispatcher__invalidatePrbEntity'),
    },
    'scripts/client/gui/shared/event_bus.pyc': {
        'EventBus.handleEvent': (
            '_EventBus__scopes', 'eventType', 'copy'),
    },
    'scripts/client/gui/battle_control/controllers/arena_load_ctrl.pyc': {
        'ArenaLoadController.invalidateArenaInfo': (
            'g_appLoader', 'showBattleLoading'),
        'ArenaLoadController.arenaLoadCompleted': (
            '_ArenaLoadController__isCompleted', 'g_appLoader',
            'showBattlePage', '_viewComponents', 'arenaLoadCompleted'),
    },
    'scripts/client/gui/battle_control/controllers/repositories.pyc': {
        'SharedControllersLocator.arenaLoad': (
            '_repository', 'getController', 'BATTLE_CTRL_ID',
            'ARENA_LOAD_PROGRESS'),
    },
    'scripts/client/gui/game_control/state_tracker.pyc': {
        'GameStateTracker.init': (
            'g_eventBus', 'addListener', 'LOBBY_VIEW_LOADED'),
        'GameStateTracker.fini': (
            'g_eventBus', 'removeListener', 'LOBBY_VIEW_LOADED'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/LobbyView.pyc': {
        'LobbyView._populate': (
            'fireEvent', 'GUICommonEvent', 'LOBBY_VIEW_LOADED'),
    },
    'scripts/client/gui/ClientHangarSpace.pyc': {
        '_VehicleAppearance.__startBuild': ('loadResourceListBG',),
        '_VehicleAppearance.__onResourcesLoaded': (
            '_VehicleAppearance__setupModel',),
        '_VehicleAppearance.__doFinalSetup': ('entity', 'model'),
    },
    'scripts/client/gui/prb_control/dispatcher.pyc': {
        '_PrbControlLoader.createBattleDispatcher': (
            '_PrbControlLoader__prbDispatcher',),
    },
    'scripts/client/gui/prb_control/__init__.pyc': {
        'prbDispatcherProperty.__get__': (
            'g_prbLoader', 'getDispatcher'),
    },
    'scripts/client/SoundGroups.pyc': {
        'SoundGroups.destroy': ('BigWorld', 'player', 'inputHandler'),
    },
    'scripts/client/CurrentVehicle.pyc': {
        '_CurrentVehicle.item': (
            '_CurrentVehicle__vehInvID', 'itemsCache', 'items',
            'getVehicle'),
    },
    'scripts/client/gui/shared/gui_items/Vehicle.pyc': {
        'Vehicle.descriptor': ('_Vehicle__descriptor',),
    },
}


EXPECTED_GLOBALS = {
    'scripts/common/AccountCommands.pyc': {
        'RES_FAILURE': -1,
        'RES_SUCCESS': 0,
        'RES_STREAM': 1,
        'CMD_SYNC_DATA': 100,
        'CMD_SYNC_SHOP': 300,
        'CMD_REQ_SERVER_STATS': 501,
        'CMD_SYNC_DOSSIERS': 600,
        'CMD_SET_LANGUAGE': 1000,
        'CMD_COMPLETE_TUTORIAL': 1150,
        'CMD_ADD_INT_USER_SETTINGS': 1600,
        'CMD_DEL_INT_USER_SETTINGS': 1601,
    },
}


EXPECTED_CLASS_CONSTANTS = {
    'scripts/client/gui/app_loader/settings.pyc': {
        'GUI_GLOBAL_SPACE_ID': {
            'LOBBY': 4,
            'BATTLE_LOADING': 5,
            'BATTLE': 6,
        },
    },
    'scripts/client/gui/shared/events.pyc': {
        'FightButtonEvent': {
            'FIGHT_BUTTON_UPDATE': 'updateFightButton',
        },
    },
    'scripts/client/gui/shared/event_bus.pyc': {
        'EVENT_BUS_SCOPE': {
            'LOBBY': 1,
        },
    },
}


_FILTER_SYNC_METHODS = ('syncGunAngles', 'syncStabilisedYPR')
EXPECTED_FILTER_SYNC_CALLS = frozenset((
    ('scripts/client/Avatar.pyc',
     'PlayerAvatar.__onSetOwnVehicleAuxPhysicsData',
     'syncStabilisedYPR'),
    ('scripts/client/Vehicle.pyc',
     'Vehicle.__startWGPhysics', 'syncGunAngles'),
    ('scripts/client/Vehicle.pyc',
     'Vehicle.set_gunAnglesPacked', 'syncGunAngles'),
    ('scripts/client/vehicle_systems/CompoundAppearance.pyc',
     'CompoundAppearance.__onModelsRefresh', 'syncGunAngles'),
))


def _signature(code):
    values = list(code.co_varnames[:code.co_argcount])
    offset = code.co_argcount
    if code.co_flags & 0x04:
        values.append('*' + code.co_varnames[offset])
        offset += 1
    if code.co_flags & 0x08:
        values.append('**' + code.co_varnames[offset])
    return tuple(values)


def _walk_code(code, path, signatures, code_objects):
    current = path + (code.co_name,)
    if code.co_name not in ('<module>', '<lambda>', '<genexpr>'):
        name = '.'.join(part for part in current if part != '<module>')
        signatures[name] = _signature(code)
        code_objects[name] = code
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            _walk_code(value, current, signatures, code_objects)


def _module_constant_globals(code):
    """Extract immediate ``NAME = constant`` assignments from a code body."""
    result = {}
    bytecode = code.co_code
    index = 0
    extended = 0
    pending = None
    has_pending = False
    while index < len(bytecode):
        operation = ord(bytecode[index])
        index += 1
        argument = None
        if operation >= opcode.HAVE_ARGUMENT:
            argument = (ord(bytecode[index]) |
                        (ord(bytecode[index + 1]) << 8) |
                        extended)
            index += 2
            if operation == opcode.EXTENDED_ARG:
                extended = argument << 16
                has_pending = False
                continue
            extended = 0
        name = opcode.opname[operation]
        if name == 'LOAD_CONST':
            pending = code.co_consts[argument]
            has_pending = True
        elif name in ('STORE_NAME', 'STORE_GLOBAL') and has_pending:
            result[code.co_names[argument]] = pending
            has_pending = False
        else:
            has_pending = False
    return result


def _read_module_contract(archive, member):
    payload = archive.read(member)
    if payload[:4] != '\x03\xf3\r\n':
        raise ValueError('%s is not CPython 2.7 bytecode' % member)
    code = marshal.loads(payload[8:])
    signatures = {}
    code_objects = {}
    _walk_code(code, (), signatures, code_objects)
    return signatures, code_objects, _module_constant_globals(code)


def _find_filter_sync_calls(archive):
    """Inventory every Python call site for unsafe retail filter syncs."""
    calls = set()
    for member in sorted(archive.namelist()):
        if not member.endswith('.pyc'):
            continue
        payload = archive.read(member)
        if payload[:4] != '\x03\xf3\r\n':
            continue
        code = marshal.loads(payload[8:])
        signatures = {}
        code_objects = {}
        _walk_code(code, (), signatures, code_objects)
        for name, item in code_objects.items():
            references = set(item.co_names)
            references.update(
                value for value in item.co_consts
                if isinstance(value, basestring))
            for method in _FILTER_SYNC_METHODS:
                if method in references:
                    calls.add((member, name, method))
    return calls


def audit(client_root):
    package_path = os.path.join(
        os.path.abspath(client_root), 'res', 'packages', 'scripts.pkg')
    if not os.path.isfile(package_path):
        raise ValueError('scripts.pkg not found: %s' % package_path)
    checked = []
    checked_literals = []
    checked_names = []
    checked_globals = []
    checked_class_constants = []
    checked_filter_sync_calls = []
    errors = []
    with zipfile.ZipFile(package_path, 'r') as archive:
        names = set(archive.namelist())
        members = (set(EXPECTED_ABI) | set(EXPECTED_CODE_LITERALS) |
                   set(EXPECTED_CODE_NAMES) | set(EXPECTED_GLOBALS) |
                   set(EXPECTED_CLASS_CONSTANTS))
        for member in sorted(members):
            if member not in names:
                errors.append('missing bytecode member: %s' % member)
                continue
            actual, code_objects, module_globals = _read_module_contract(
                archive, member)
            for name, expected_args in sorted(
                    EXPECTED_ABI.get(member, {}).items()):
                actual_args = actual.get(name)
                if actual_args is None:
                    errors.append('%s: missing %s' % (member, name))
                elif actual_args != expected_args:
                    errors.append(
                        '%s: %s args %r, expected %r' %
                        (member, name, actual_args, expected_args))
                else:
                    checked.append('%s:%s' % (member, name))
            for name, expected_literals in sorted(
                    EXPECTED_CODE_LITERALS.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for literals' %
                                  (member, name))
                    continue
                constants = set(value for value in code.co_consts
                                if isinstance(value, basestring))
                for literal in expected_literals:
                    if literal not in constants:
                        errors.append('%s: %s missing literal %r' %
                                      (member, name, literal))
                    else:
                        checked_literals.append(
                            '%s:%s:%s' % (member, name, literal))
            for name, expected_names in sorted(
                    EXPECTED_CODE_NAMES.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for names' %
                                  (member, name))
                    continue
                code_names = set(code.co_names)
                for code_name in expected_names:
                    if code_name not in code_names:
                        errors.append('%s: %s missing code name %r' %
                                      (member, name, code_name))
                    else:
                        checked_names.append(
                            '%s:%s:%s' % (member, name, code_name))
            for name, expected_value in sorted(
                    EXPECTED_GLOBALS.get(member, {}).items()):
                if name not in module_globals:
                    errors.append('%s: missing constant global %s' %
                                  (member, name))
                elif module_globals[name] != expected_value:
                    errors.append('%s: %s is %r, expected %r' %
                                  (member, name, module_globals[name],
                                   expected_value))
                else:
                    checked_globals.append('%s:%s=%r' %
                                           (member, name, expected_value))
            for class_name, expected_constants in sorted(
                    EXPECTED_CLASS_CONSTANTS.get(member, {}).items()):
                class_code = code_objects.get(class_name)
                if class_code is None:
                    errors.append('%s: missing class body %s for constants' %
                                  (member, class_name))
                    continue
                class_constants = _module_constant_globals(class_code)
                for name, expected_value in sorted(
                        expected_constants.items()):
                    if name not in class_constants:
                        errors.append('%s: %s missing constant %s' %
                                      (member, class_name, name))
                    elif class_constants[name] != expected_value:
                        errors.append('%s: %s.%s is %r, expected %r' %
                                      (member, class_name, name,
                                       class_constants[name], expected_value))
                    else:
                        checked_class_constants.append(
                            '%s:%s.%s=%r' %
                            (member, class_name, name, expected_value))
        actual_filter_sync_calls = _find_filter_sync_calls(archive)
        missing_filter_calls = (
            EXPECTED_FILTER_SYNC_CALLS - actual_filter_sync_calls)
        unexpected_filter_calls = (
            actual_filter_sync_calls - EXPECTED_FILTER_SYNC_CALLS)
        for item in sorted(missing_filter_calls):
            errors.append('missing filter sync call site: %s:%s:%s' % item)
        for item in sorted(unexpected_filter_calls):
            errors.append('unexpected filter sync call site: %s:%s:%s' % item)
        checked_filter_sync_calls.extend(
            '%s:%s:%s' % item
            for item in sorted(
                actual_filter_sync_calls & EXPECTED_FILTER_SYNC_CALLS))
    if errors:
        raise ValueError('; '.join(errors))
    return {
        'clientRoot': os.path.abspath(client_root),
        'pythonRuntime': '%d.%d.%d' % sys.version_info[:3],
        'checkedSignatures': len(checked),
        'checkedConsumerLiterals': len(checked_literals),
        'checkedCodeNames': len(checked_names),
        'checkedConstantGlobals': len(checked_globals),
        'checkedClassConstants': len(checked_class_constants),
        'checkedFilterSyncCalls': len(checked_filter_sync_calls),
        'contracts': checked,
        'consumerLiterals': checked_literals,
        'codeNames': checked_names,
        'constantGlobals': checked_globals,
        'classConstants': checked_class_constants,
        'filterSyncCalls': checked_filter_sync_calls,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Audit exact WoT #1513 PYC signatures read-only.')
    parser.add_argument('client_root')
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (2, 7):
        parser.error('this auditor must run under CPython 2.7')
    try:
        report = audit(args.client_root)
    except (IOError, KeyError, ValueError, zipfile.BadZipfile) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
