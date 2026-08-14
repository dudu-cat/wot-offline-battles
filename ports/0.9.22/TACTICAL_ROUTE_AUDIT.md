# Pinned #1513 tactical-route audit

Audit date: 2026-08-11

Scope: the 41 standard-battle graphs in `ports/0.9.22/navgraphs`, the
reviewed tactical source in `ai/reviewed_routes_20260811.py`, and the exact
World of Tanks `0.9.22.0.1-cn-1513` compiled-space inputs.

## Current verdict

Every supported map now has an explicit human-review disposition. Thirty-eight
maps contain reviewed strategic geometry. The Ensk review deliberately removes
`rail_yard`, leaving 113 reviewed route ids. `34_redshire`, `95_lost_city`, and
`100_thepit` were explicitly accepted unchanged. Himmelsdorf's local
`rear_guard` route was not redrawn, leaving the complete graph set at 123 route
ids and 246 team-route records.

The review strokes are strategic corridor intent, not literal locomotion
polylines. Sparse gates select the requested side of the map and tactical lane;
the pinned baker projects them onto the four-metre safe graph and recomputes
each direction around exact compiled collision, water, grade, clearance, and
fatal-edge constraints.

The accepted maps retain their previous route definitions. All 41 graph files
were nevertheless regenerated because this review uncovered an incorrect
`BSP\x02` decoder in the old graph baker. Therefore "accepted unchanged" means
route intent, not byte-identical generated JSON.

## Interpretation rules

1. A red stroke fixes the macro lane and map side. It does not authorize a
   direct segment through a building, rock, lake, fatal edge, or disconnected
   graph island.
2. Route ids, role weights, capacity, and risk remain stable except for the
   explicit Ensk removal and its balanced `7 + 7` capacities. Each ordinary
   through-route is baked once from the team-one anchor, then team two receives
   that exact validated geometry in reverse. The final graph validator still
   proves every reverse directed segment. Local terminal routes remain
   team-specific.
3. Each reviewed through-route has one representative hard gate. Other points
   shape the safe search corridor without forcing a U-turn around annotation
   jitter or a projected obstacle pocket.
4. `34_redshire`, `95_lost_city`, and `100_thepit` remain the three explicit
   accepted controls. Their canonical filenames do not carry an `(ok)` suffix.
5. Himmelsdorf's `rear_guard` remains a fourth, local artillery route and is
   reviewed manually rather than extracted as a through-route.

## Exact topology corrections

The route review exposed three cases where the previous safe graph contradicted
the retail map rather than the annotation:

- The old `BSP\x02` reader interpreted the v2 ABI size fields as legacy counts.
  The exact format has a 28-byte header, a 32-byte bounding box, 40-byte
  `WorldTriangle` records, shared offsets, and 40-byte packed nodes. The new
  parser validates every count and consumes the section exactly; it does not
  fall back to render geometry or silently skip malformed resources.
- `07_lakeville/lake_road` is a real lakeside corridor. Four-metre sampling
  rejected one safe diagonal only because an adjacent cardinal cell carried an
  edge bit. The adapter restores exactly the verified reversible link
  `(-122, 46) <-> (-118, 42)` after repeating the complete water, obstacle,
  grade, edge-clearance, and sub-cell checks. No generic diagonal rule was
  relaxed.
- `17_munchen/west_streets` crosses below a bridge whose deck and lower road
  occupy the same x/z cells. A pinned adapter replaces only five lower-layer
  cells, preserves adjacent deck cells and validates at least 8.40 m overhead
  clearance before installing the reversible underpass chain.
- `59_asia_great_wall` has a real vehicle tunnel through the eastern gatehouse,
  but the default four-metre x lattice misses its centre. Only this map, at a
  four-metre cell size, uses an x-phase sampling bound that produces the
  `x=404` tunnel chain. Public gameplay bounds remain `[-500, -500, 500, 500]`.
  The baker fails closed unless all three tunnel nodes are safe and reversible
  while the neighbouring `x=400` and `x=408` wall nodes remain blocked. This
  preserves the distinct upper/right `wall_pass` and `valley` lanes without a
  three-metre graph, a raw-triangle bypass, or a relaxed vehicle margin.
- Round 4 adds six more narrow, map-local corrections rather than weakening a
  global graph rule: the El Hallouf south slope, two North America fords, the
  Great Wall north saddle, and one safe diagonal each on Winter and Stalingrad.
  Every correction repeats the raw terrain, water, obstacle, grade and reverse
  link checks and proves neighbouring closed cells remain closed.

Three requested strokes are deliberately not represented as if they were
safe. The new `04_himmelsdorf/banana` and
`86_himmelsdorf_winter/banana` strokes cross the same authored static
`bld413_thouse` instance; exact BSP material remapping confirms it is neither a
destructible nor a no-collide raster error. Their previous safe banana geometry
therefore remains. Ensk's west-city annotation also asks a full-width vehicle
to use a gap that passes only after reducing the proved 2.15 m clearance
margin, so the physical building detour remains even though the orange route
has been removed. These limitations are visible in the Round 4 review pack.

## Post-bake census

- The manifest covers exactly 41 maps and every graph hash matches. There are
  246 team-route records: 39 maps have three per team, Ensk has two, and
  Himmelsdorf has four.
- Every graph is one retained component with `largest_fraction == 1.0`.
  Through-routes begin at their own spawn anchor, end at the opposing anchor,
  contain 16 validated waypoints, and never occupy a fatal water or edge cell.
- The maximum route detour is `1.342x` on `13_erlenberg`; the maximum opening
  regression is `92.687 m` on `28_desert`. Both remain below the `2.0x` and
  `120 m` safety gates.
- All reviewed routes bake without soft fallback. The only remaining fallbacks
  are the two accepted `95_lost_city/outskirts` directions. The graph-set
  maximum projection, `68.007 m`, belongs to accepted `100_thepit`; reviewed
  Great Wall projects by at most `9.22 m`.
- The final Great Wall graph has SHA-256
  `c3264b442e967b0b4b2bdc78a09628f90c25bd5b5ce96a9cb9c12a18a158333a`,
  public bounds `[-500, -500, 500, 500]`, origin `[-500, -498]`, and dimensions
  `251 x 250`.

## Review inventory

The 38 changed maps are:

`01_karelia`, `02_malinovka`, `04_himmelsdorf`, `05_prohorovka`,
`06_ensk`, `07_lakeville`, `08_ruinberg`, `10_hills`, `11_murovanka`,
`13_erlenberg`, `14_siegfried_line`, `17_munchen`, `18_cliff`,
`19_monastery`, `22_slough`, `23_westfeld`, `28_desert`,
`29_el_hallouf`, `31_airfield`, `33_fjord`, `35_steppes`,
`36_fishing_bay`, `37_caucasus`, `38_mannerheim_line`,
`44_north_america`, `45_north_america`, `47_canada_a`,
`59_asia_great_wall`, `63_tundra`, `73_asia_korea`, `83_kharkiv`,
`84_winter`, `86_himmelsdorf_winter`, `92_stalingrad`, `101_dday`,
`103_ruinberg_winter`, `112_eiffel_tower_ctf`, and `114_czech`.

The accepted controls are `34_redshire`, `95_lost_city`, and `100_thepit`.

## Round 4 render and acceptance

The final candidate is rendered separately so neither annotation batch nor the
Round 2 output is overwritten:

```sh
python3 tools/render_tactical_routes.py \
  --client-root "/path/to/World_of_Tanks_0.09.22.00.01_CH_1513_HD" \
  --navgraph-dir ports/0.9.22/navgraphs \
  --output-dir "/path/to/WoT-0.9.22-Tactical-Routes-Review-Round4-All" \
  --size 1200 --clean
```

Automated acceptance requires the 41-file manifest, schema, graph arrays,
component, endpoint, hazard, detour, opening-regression, route-intent, and
map-specific topology gates to pass. The principal tests are
`test_port_0922_navigation_baker.py`,
`test_port_0922_tactical_route_review.py`, and `test_port_0922_ai.py`.

This is still a review candidate, not a new client release. Native #1513 play
must eventually verify actual Bot departure, tunnel/bridge traversal, obstacle
avoidance, congestion behaviour, and late-battle lane choice before these
graphs are packaged.
