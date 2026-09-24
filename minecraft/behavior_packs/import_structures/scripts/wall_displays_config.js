// All installation coordinates live here. Detection never changes world blocks.
export const WALL_DISPLAYS = Object.freeze({
  dimension: "minecraft:overworld",
  video: { x: 132, width: 11, height: 4, minBottom: 251, maxBottom: 260,
    planes: [-87, -86, -85], audienceRadius: 16, entity: "ichiyon:video_screen" },
  map: { leftX: 15, width: 3, height: 3, minBottom: 90, maxBottom: 98,
    planes: [243, 244, 245], facing: 2 },
});
