"""Descriptor projections donated by a #1513 client.

The server process has no access to the client's item definitions. A
connected client projects each vehicle descriptor into plain JSON once per
battle; this module wraps that JSON so the engine-free combat and physics
laws can read it through both surfaces a live VehicleDescr exposes: plain
mappings and attribute access.
"""


class Projection(dict):
    """A dict whose keys are also readable as attributes, recursively."""

    def __init__(self, raw):
        super(Projection, self).__init__(
            (key, wrap(value)) for key, value in raw.items())

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def wrap(value):
    """Wrap dicts as projections and lists as tuples, recursively."""
    if isinstance(value, Projection):
        return value
    if isinstance(value, dict):
        return Projection(value)
    if isinstance(value, (list, tuple)):
        return tuple(wrap(item) for item in value)
    return value


class DescriptorStore(object):
    """Donated projections for one battle, keyed by vehicle type name."""

    def __init__(self, projections=None):
        self._projections = {}
        if projections:
            for name, raw in projections.items():
                self.add(name, raw)

    def add(self, name, raw):
        if not isinstance(raw, dict):
            raise ValueError('descriptor projection is not an object')
        self._projections[str(name)] = wrap(raw)

    def get(self, name):
        return self._projections.get(str(name))

    def names(self):
        return sorted(self._projections)

    def __len__(self):
        return len(self._projections)
