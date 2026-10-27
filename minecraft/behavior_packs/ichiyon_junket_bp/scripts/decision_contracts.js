import { eligibleSpecialDecisions, specialDecisionWindow } from "./special_decisions.js";
import { eligibleDecisionSeats, decisionWindow } from "./remaining_decisions.js";

const special = new Set(["sparrow", "temperance", "lucy", "silence", "jinn", "ace"]);
export function decisionSeats(id, state) {
  if (!state || state.phase === "finished") return [];
  return special.has(id) ? eligibleSpecialDecisions(id, state) : eligibleDecisionSeats(id, state);
}
export function decisionKey(id, state, seat) {
  return special.has(id) ? specialDecisionWindow(id, state, seat) : decisionWindow(id, state, seat);
}

// Optional source actions remain legal while the opponent answers. Do not use
// the table-wide revision as their input fence; retain the acting player's
// resource snapshot and the source phase instead.
export function supplementalKeys(id, state, seat) {
  if (!state || state.phase === "finished") return {};
  const context = [id, seat, state.phase, state.round, state.turn, state.set];
  if (id === "grail" && state.poisonCup[seat] + state.holyCup[seat] > 0) {
    return { drink: JSON.stringify([...context, state.poisonCup[seat], state.holyCup[seat]]) };
  }
  if (id === "mine" && state.keys[seat] > 0) {
    // Two players may spend different keys concurrently. The reducer checks
    // the selected target/skill cap atomically against the latest state.
    return { key: JSON.stringify([...context, state.keys[seat]]) };
  }
  return {};
}
