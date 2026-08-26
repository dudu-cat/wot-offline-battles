"""Shared #1513 vehicle-module fitting rules."""


def top_component(components):
    """Return the highest-level component, breaking ties by list order."""
    best = None
    for index, descriptor in enumerate(components or ()):
        key = (int(getattr(descriptor, 'level', 1)), index)
        if best is None or key >= best[0]:
            best = (key, descriptor)
    return None if best is None else best[1]


def install_top_modules(descriptor):
    """Fit the top module of every slot in the order #1513 accepts."""
    vehicle_type = descriptor.type
    chassis = top_component(getattr(vehicle_type, 'chassis', ()))
    if chassis is not None:
        descriptor.installComponent(chassis.compactDescr, 0)

    # #1513 rejects turrets through installComponent.  installTurret takes
    # the compatible turret/gun pair and resolves the final hull correctly.
    for position, turrets in enumerate(
            getattr(vehicle_type, 'turrets', ()) or ()):
        turret = top_component(turrets)
        gun = None if turret is None else top_component(
            getattr(turret, 'guns', ()))
        if gun is not None:
            descriptor.installTurret(
                turret.compactDescr, gun.compactDescr, position)

    for attribute in ('engines', 'radios', 'fuelTanks'):
        component = top_component(getattr(vehicle_type, attribute, ()))
        if component is not None:
            descriptor.installComponent(component.compactDescr, 0)
    return descriptor
