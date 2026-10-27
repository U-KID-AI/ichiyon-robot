// Synthetic adapter fixture. No original archive or original-room coordinates.
export const SPARROW_REFERENCE = {
  id: "sparrow", title: "Synthetic Sparrow", special_runtime: true,
  source: "synthetic-fixture", distribution: "private-local-only",
  structure: "junket:sparrow_hall", origin: [0, 64, 0], size: [12, 8, 12],
  provider: { collection: "junket:venue_fixture", role_property: "junket:sparrow_role" },
  destination: { x: 2, y: 65, z: 2 }, return_point: { x: 2, y: 65, z: 2 }, rotation: 90,
  seats: [{ x: 4, y: 65, z: 6 }, { x: 7, y: 65, z: 6 }],
  fixtures: [
    { role: "reception", type: "minecraft:npc", location: [2, 65, 3], name: "Reception", tags: ["junket:reception"] },
    { role: "return", type: "minecraft:npc", location: [3, 65, 2], name: "Return", tags: ["junket:sparrow_return"] },
    { role: "bigbox", type: "junket:bigbox", location: [4, 66, 6], name: "Big", tags: ["junket:venue_box"], properties: { "junket:box": "big" } },
    { role: "littlebox", type: "junket:littlebox", location: [7, 66, 6], name: "Small", tags: ["junket:venue_box"], properties: { "junket:box": "small" } },
    { role: "diamond", type: "junket:diamond", location: [5, 67, 5], name: "Gem", tags: [] },
    { role: "tv", type: "junket:tv", location: [5, 69, 1], rotation: 180, name: "Monitor", tags: [] },
  ],
  points: {
    box_pose: [{ x: 4, y: 66, z: 6 }, { x: 7, y: 66, z: 6 }],
    box_offsets: { bigbox: { x: 0, y: 0, z: 0 }, littlebox: { x: 0, y: -0.1, z: 1 } },
    partition: { x: 5, y: 66, z: 5 }, stage_center: { x: 5, y: 66, z: 5 },
  },
  block_overlays: [{ location: { x: 5, y: 65, z: 5 }, block: "minecraft:green_carpet" }],
  probes: [
    { label: "floor", location: { x: 2, y: 64, z: 2 }, blocks: ["minecraft:red_wool"] },
    { label: "feet", location: { x: 2, y: 65, z: 2 }, blocks: ["minecraft:air", "minecraft:light_block_12"] },
    { label: "head", location: { x: 2, y: 66, z: 2 }, blocks: ["minecraft:air", "minecraft:light_block_12"] },
    { label: "table", location: { x: 6, y: 65, z: 5 }, blocks: ["minecraft:green_carpet"] },
  ],
};
