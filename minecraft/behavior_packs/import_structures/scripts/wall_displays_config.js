// All installation coordinates live here. Detection never changes world blocks.
export const WALL_DISPLAYS = Object.freeze({
  dimension: "minecraft:overworld",
  video: { x: 132, width: 11, height: 4, minBottom: 251, maxBottom: 260,
    planes: [-87, -86, -85], audienceRadius: 64, entity: "ichiyon:video_screen" },
  map: { leftX: 15, width: 3, height: 3, minBottom: 90, maxBottom: 98,
    planes: [243, 244, 245], facing: 2 },
});

export const BIG_VIDEO_DISPLAY = Object.freeze({
  id: "big", dimension: "minecraft:overworld",
  video: {
    anchor: { x: -17, y: 75, z: -187 },
    minX: -21, maxX: -13, width: 24, height: 11, minBottom: 62, maxBottom: 76,
    planes: [-192, -191, -190, -189, -188, -187, -186, -185, -184],
    materials: ["minecraft:black_concrete", "minecraft:black_wool", "minecraft:black_terracotta",
      "minecraft:coal_block", "minecraft:blackstone", "minecraft:polished_blackstone", "minecraft:obsidian"],
    floorButton: { anchor: { x: -16, y: 66, z: -185 }, radius: 3 },
    audience: { marginX: 1, near: 1, far: 22, minY: -3, maxY: 4 },
    entity: "ichiyon:video_screen_big",
  },
});

// Observed via read-only vanilla block queries on 2026-09-25. The observer's
// position is not a wall coordinate; keep a bounded unique detector around it.
export const AKKI_VIDEO_DISPLAY = Object.freeze({
  id: "akki", dimension: "minecraft:overworld",
  video: {
    minX: -27, maxX: -25, width: 13, height: 4, minBottom: 101, maxBottom: 103,
    planes: [231, 232, 233], materials: ["minecraft:white_concrete"],
    controlButton: { mode: "wall", anchor: { x: -20, y: 104, z: 237 }, radius: 2 },
    audience: { marginX: 0, near: 0, far: 5, minY: -1, maxY: 5 },
    entity: "ichiyon:video_screen_akki",
  },
});
