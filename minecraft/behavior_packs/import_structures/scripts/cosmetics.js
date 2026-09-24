import { world, system, ItemStack, GameMode } from "@minecraft/server";
import { ActionFormData } from "@minecraft/server-ui";
import { catalog } from "./cosmetics_catalog.js";
import { createCosmetics } from "./cosmetics_core.js";

export const cosmetics = createCosmetics({ catalog, world, system, ItemStack, ActionFormData, GameMode });
export const cosmeticsDigest = catalog.digest;
export const managedPosters = catalog.posters || [];
cosmetics.start();
