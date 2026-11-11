import { STRATEGY_GAMES, FIRE_CONTROL_CARDS, pigAttackTargets } from "./strategy_games.js";
import { BOARD_GAMES, rabbitLegalMoves, rabbitLegalTrapFloors, walkerFloorDistance } from "./board_games.js";

const games = [...STRATEGY_GAMES, ...BOARD_GAMES];
const expect = (condition, message) => { if (!condition) throw new Error(message); };
const clone = value => JSON.parse(JSON.stringify(value));

// These traces execute the installed reducers, not a second reimplementation.
// Spatial observations are deliberately synthetic; they are never client
// movement, collision, mobile-form or source-Java execution evidence.
export function runRemainingRuleTrace(gameId) {
  const game = games.find(value => value.id === gameId);
  if (!game) throw new Error(`Unknown remaining rule trace: ${gameId}`);
  let state = game.create(), actions = 0, systemEvents = 0, steps = 0;
  const phases = new Set([state.phase]), actors = new Set();
  const act = (seat, action, value) => {
    const before = JSON.stringify(state);
    state = game.advance(state, seat, action, value); actions++; actors.add(seat);
    expect(JSON.stringify(state) !== before, `${gameId}: action did not advance ${action}`);
  };
  const system = (action = "tick", value) => {
    state = game.systemAdvance(state, action, value); systemEvents++;
  };
  let environment = "none (pure rules)";
  while (state.phase !== "finished" && steps++ < 150000) {
    phases.add(state.phase);
    if (gameId === "grail") {
      if (state.phase === "arrange") {
        // Submit potion first to verify source simultaneous arrangement.
        if (state.racks.potion.holyIndex === null) act(state.potionOwner, "arrange", { holyIndex: 0 });
        else act(state.cupOwner, "arrange", { trueIndex: 0 });
      } else if (state.phase === "cup") act(state.actor, "cup", state.exchange === 0 ? 1 : 0);
      else if (state.phase === "potion") act(state.actor, "potion", state.exchange === 0 ? 0 : 1);
      else if (state.phase === "final_drink" && state.actor !== null) act(state.actor, "drink");
      else system();
    } else if (gameId === "auction") {
      if (state.phase === "bid") {
        const seat = state.actor;
        const options = [4, 3, 2, 1].filter(card => !state.selected[seat].includes(card));
        act(seat, "bid", options[(seat + state.set + state.round) % options.length]);
      } else if (state.phase === "set_result") system("next_set");
      else system("tick");
    } else if (gameId === "fire") {
      if (state.phase === "choice") {
        const seat = state.choices[1] === null ? 1 : 0;
        const order = seat === 0
          ? ["3:hot", "1:hot", "2:hot", "3:ice", "1:ice", "2:ice"]
          : ["1:hot", "2:hot", "3:hot", "1:ice", "2:ice", "3:ice"];
        const id = order.find(card => !state.used[seat].includes(card));
        const card = Object.values(FIRE_CONTROL_CARDS).find(value => value.id === id);
        act(seat, "choice", { number: Number(card.id[0]), heat: card.id.endsWith("hot") ? 1 : -1 });
      } else system();
    } else if (gameId === "pig") {
      if (state.phase === "choose") {
        const seat = state.actor;
        const order = seat === 0 ? [4, 1, 2, 3] : [1, 2, 3, 4];
        act(seat, "choose", { card: order.find(card => !state.used[seat].includes(card)) });
      } else if (state.phase === "attack") act(state.actor, "target", pigAttackTargets(state, state.actor)[0]);
      else system();
    } else if (gameId === "light") {
      if (state.phase === "guard_arrange") act(state.actor, "arrange", { guards: [0, 1, 2] });
      else if (state.phase === "tourist_arrange") act(state.actor, "arrange", { tourists: [1, 0, 2] });
      else system();
    } else if (gameId === "mine") {
      if (state.phase === "arrange") act(state.actor, "arrange", { cards: [0, 1, 2, 3] });
      else if (state.phase === "choose") act(state.actor, "mine",
        { indices: [1, 2, 3, 0].filter(index => !state.opened[state.actor].includes(index)).slice(0, state.chooseCount) });
      else system("tick", { ticks: 1 });
    } else if (gameId === "hope") {
      environment = "synthetic safe-pad observations for two human seats";
      if (["choose", "reveal"].includes(state.phase)) {
        const pad = state.padSymbols.findIndex(symbol => !state.hazards.includes(symbol)) + 1;
        if (state.choices.some(choice => choice?.pad !== pad)) system("position", [pad, pad]);
        else system();
      } else system();
    } else if (gameId === "clock") {
      environment = "synthetic water landing after selected floor opens";
      if (state.phase === "setup") act(state.actor, "time_confirm");
      else if (state.phase === "move" && !state.selectionComplete) act(state.actor, "sector_confirm");
      else if (state.phase === "drop" && game.view(state).floorOpen) system("land", [1]);
      else system();
    } else if (gameId === "walker") {
      environment = "synthetic adjacent movement, bomb choice and water landing";
      if (state.phase === "walk") {
        if (state.movedSteps[state.walker] === 0) {
          const from = state.positions[state.walker];
          const destination = Array.from({ length: 37 }, (_, index) => index + 1)
            .find(tile => walkerFloorDistance(from, tile) === 1 && !state.deadFloors.includes(tile));
          act(state.walker, "position", destination);
        } else act(state.walker, "stop");
      } else if (state.phase === "bomb_setup") act(state.breaker, "bomb_confirm");
      else if (state.phase === "aftermath" && !state.water.some(Boolean)) system("water", [state.walker]);
      else system();
    } else if (gameId === "garden") {
      environment = "synthetic losing-seat landing after exact-100 hold opens floor";
      if (state.phase === "choose" && state.loads[1] === 100) system("tick", 1);
      else if (state.phase === "choose") {
        const seat = state.choices[1] === null ? 1 : 0;
        act(seat, "choose", "open");
        system("tick", 1);
      } else if (state.phase === "drop" && state.dropRemaining === 0) system("land", state.loser);
      else system("tick", 1);
    } else if (gameId === "rabbit") {
      environment = "synthetic legal human positions and falling-player water observations";
      if (state.phase === "trap") act(1, "trap", rabbitLegalTrapFloors(state)[0]);
      else if (state.phase === "move") {
        const moves = rabbitLegalMoves(state);
        const target = [...moves].sort((a, b) => ((b - 1) % 12) - ((a - 1) % 12))[0];
        system("position", target);
      } else if (state.phase === "drop" && state.phaseRemaining === 0) {
        system("land", state.falling ?? []);
      } else system();
    }
    // A serialization round-trip in the middle of every trace checks the
    // state itself does not rely on closures, entity references or Sets.
    if (steps === 10) state = clone(state);
  }
  expect(state.phase === "finished", `${gameId}: no terminal state after ${steps} steps (${state.phase})`);
  expect(actions > 0 || gameId === "hope", `${gameId}: no game decisions exercised`);
  if (gameId === "grail") expect(state.holy.every(value => value === 1000), "Grail: ten rounds of holy water");
  if (gameId === "garden") expect(state.reason === "box_100" && state.loads[1] === 100, "Garden exact-100 result");
  if (gameId === "light") expect(state.reason === "hearts", "Light seven-heart result");
  if (gameId === "auction") expect(["payment", "diamonds"].includes(state.reason), "Auction payment or diamonds result");
  return { gameId, passed: true, actions, systemEvents, steps,
    phases: [...phases, "finished"], seatsActed: [...actors].sort(),
    terminal: { phase: state.phase, winner: state.winner ?? null,
      winnerTeam: state.winnerTeam ?? null, reason: state.reason },
    environment, scope: "installed-reducer deterministic gameplay trace; not Java execution or client playthrough" };
}

export function runRemainingRuleTraces() {
  return games.map(game => runRemainingRuleTrace(game.id));
}
