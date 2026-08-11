'''res_mods override of the stock OfflineEntity (res/scripts/client).

Engine-assigned entity ids can collide with vehicle/inventory ids, so native
code that probes BigWorld.entity(playerVehicleID) - e.g. ArcadeControlMode.
__activateAlternateMode on Shift, the arcade<->sniper scroll switch - may get
one of these stubs back and then reads .isStarted / .appearance.isUnderwater().
The stock class had neither attribute: the AttributeError killed the key event
and the strategic (SPG) camera could not be entered for the rest of the battle.
Looking like a not-yet-started Vehicle (isStarted False) short-circuits those
checks safely.
'''

import BigWorld


class OfflineEntity(BigWorld.Entity):
    isStarted = False
    appearance = None
    typeDescriptor = None
    wgPhysics = None

    def __init__(self):
        pass

    def prerequisites(self):
        return []

    def onEnterWorld(self, prereqs):
        pass

    def onLeaveWorld(self):
        pass

    # WGVehiclePhysics2 normally owns a retail Vehicle and may dispatch these
    # callbacks while resolving static geometry. OfflineEntity is also used by
    # the opt-in native-physics capability probe; no-op handlers keep that probe
    # isolated from gameplay damage and destructible state.
    def onStaticCollision(self, energy, point, normal, miscFlags):
        pass

    def _isDestructibleMayBeBroken(self, chunkID, itemIndex, matKind,
            itemFilename, itemScale, vehicleSpeed):
        return False

    def showVehicleCollisionEffect(self, position):
        pass
