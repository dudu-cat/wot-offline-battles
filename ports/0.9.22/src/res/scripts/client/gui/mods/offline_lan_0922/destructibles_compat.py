# -*- coding: utf-8 -*-
"""Expose the 0.8.2 AreaDestructibles surface on pinned #1513.

The authority module retains the 0.8.2 law with explicit #1513 ABI fixes.
#1513 moved the encoders and damage-type constants to
``DestructiblesCache``; this adapter restores only those moved names.
"""


_INSTALLED = False


def install(area_module=None, cache_module=None):
    global _INSTALLED
    if _INSTALLED:
        return True

    if area_module is None:
        import AreaDestructibles as area_module
    if cache_module is None:
        import DestructiblesCache as cache_module

    AreaDestructibles = area_module
    DestructiblesCache = cache_module

    moved_functions = {
        'chunkIDFromPosition': DestructiblesCache.chunkIDFromPosition,
        'encodeFallenTree': DestructiblesCache.encodeFallenTree,
        'encodeFallenColumn': DestructiblesCache.encodeFallenColumn,
        'encodeFragile': DestructiblesCache.encodeFragile,
        'encodeDestructibleModule':
            DestructiblesCache.encodeDestructibleModule,
    }
    moved_constants = {
        'DESTR_TYPE_TREE': DestructiblesCache.DESTR_TYPE_TREE,
        'DESTR_TYPE_FALLING_ATOM':
            DestructiblesCache.DESTR_TYPE_FALLING_ATOM,
        'DESTR_TYPE_FRAGILE': DestructiblesCache.DESTR_TYPE_FRAGILE,
        'DESTR_TYPE_STRUCTURE': DestructiblesCache.DESTR_TYPE_STRUCTURE,
        '_DAMAGE_TYPE_TREE': DestructiblesCache.DESTR_TYPE_TREE,
        '_DAMAGE_TYPE_COLUMN': DestructiblesCache.DESTR_TYPE_FALLING_ATOM,
        '_DAMAGE_TYPE_FRAGILE': DestructiblesCache.DESTR_TYPE_FRAGILE,
        '_DAMAGE_TYPE_MODULE': DestructiblesCache.DESTR_TYPE_STRUCTURE,
    }
    for name, value in moved_functions.items():
        if not hasattr(AreaDestructibles, name):
            setattr(AreaDestructibles, name, value)
    for name, value in moved_constants.items():
        if not hasattr(AreaDestructibles, name):
            setattr(AreaDestructibles, name, value)

    _INSTALLED = True
    return True
