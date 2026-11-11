// Isolated real-rule samples for offline and explicitly requested headless
// lifecycle checks. No manager, persistent table, timer or player is created.
import { sparrowGame } from "./sparrow.js";
import { temperanceGame } from "./temperance.js";
import { lucyGame } from "./lucy.js";
import { silenceGame } from "./silence.js";
import { jinnGame } from "./jinn.js";
import { aceGame } from "./ace.js";
import { STRATEGY_GAMES } from "./strategy_games.js";
import { BOARD_GAMES, walkerFloorDistance, rabbitLegalTrapFloors } from "./board_games.js";

export const QUALITY_GAMES = [sparrowGame, temperanceGame, lucyGame, silenceGame,
  jinnGame, aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES];
export function makeQualityCase(id, suffix = "probe") {
  const game = QUALITY_GAMES.find(value => value.id === id);
  if (!game) throw new Error(`unknown lifecycle game: ${id}`);
  const count = ["ace", "auction", "pig"].includes(id) ? 4 : 2;
  const players = Array.from({ length: count }, (_, index) => `junket-quality-${suffix}-${index + 1}`);
  const initial = game.create(count);
  let raw = initial, systemSteps = 0;
  if (id === "light") {
    while (raw.phase === "intro" && systemSteps < 1000) {
      raw = game.systemAdvance(raw, "tick"); systemSteps++;
    }
  }
  const requests = {
    sparrow: [0, "bet", 1000000], temperance: [1, "arrange", [1, -2, 3]],
    lucy: [0, "lock", [true, false, false, false, false]], silence: [raw.actor ?? 0, "arrange", [0, 20, 30]],
    jinn: [0, "choose", "gold"], ace: [raw.parent ?? 0, "table_bet", 10],
    grail: [0, "arrange", { trueIndex: 0 }], auction: [0, "bid", 1],
    fire: [0, "choice", { number: 1, heat: 1 }], pig: [0, "choose", { card: 1 }],
    light: [raw.actor ?? 0, "place", { role: "guard", slot: 0 }],
    mine: [raw.actor ?? 0, "arrange", { cards: [0, 1, 2, 3] }],
    hope: [0, "floor", 1], clock: [0, "minute_plus"], garden: [0, "choose", "double"],
    walker: [0, "walk", id === "walker" ? Array.from({ length: 37 }, (_, i) => i + 1)
      .find(floor => walkerFloorDistance(raw.positions[0], floor) === 1) : 1],
    rabbit: [1, "trap", id === "rabbit" ? rabbitLegalTrapFloors(raw)[0] : 1],
  };
  const [seat, action, value] = requests[id];
  const next = game.advance(raw, seat, action, value);
  const table = (state, token) => ({ id, gameId: id, players: [...players], token,
    state: game.view ? game.view(state) : state });
  return { game, initial: table(initial, 1), advanced: table(next, 2),
    action: { seat, action, value, systemSteps }, player_test: false, full_match: false };
}
