// Decision windows for the eleven source games implemented by the strategy
// and spatial reducers. A timer tick or somebody else's sealed choice does
// not invalidate a player's still-legal form. Physical movement remains live.
const pending = values => values.map((value, seat) => value === null ? seat : -1)
  .filter(seat => seat >= 0);
const actorIn = (state, phases) => phases.includes(state.phase) && Number.isInteger(state.actor)
  ? [state.actor] : [];

export function eligibleDecisionSeats(gameId, state) {
  if (!state || state.phase === "finished") return [];
  switch (gameId) {
    case "grail":
      if (state.phase === "final_drink") return [0, 1].filter(seat =>
        state.poisonCup[seat] + state.holyCup[seat] > 0);
      if (state.phase === "arrange") return [state.cupOwner, state.potionOwner]
        .filter(seat => seat === state.cupOwner
          ? state.racks.cup.trueIndex === null : state.racks.potion.holyIndex === null);
      return actorIn(state, ["cup", "potion"]);
    case "auction": return actorIn(state, ["bid"]);
    case "fire": return state.phase === "choice" ? pending(state.choices) : [];
    case "pig": return actorIn(state, ["choose", "attack"]);
    case "light": return actorIn(state, ["guard_arrange", "tourist_arrange"]);
    case "mine": return actorIn(state, ["arrange", "choose"]);
    // Hope's short reveal window is real-time walking. Opening a form would
    // stop movement precisely when the falling-floor decision has to happen.
    case "hope": return [];
    case "clock": return state.selectionComplete && state.phase === "move"
      ? [] : actorIn(state, ["setup", "move"]);
    // Walking/dodging must not be interrupted by modal forms. The stop action
    // remains available through the manual resume menu while walking.
    case "walker": return actorIn(state, ["bomb_setup"]);
    case "garden": return state.phase === "choose" ? pending(state.choices) : [];
    // During Rabbit's move window the human physically crosses the board.
    case "rabbit": return actorIn(state, ["trap"]);
    default: return [];
  }
}

export function decisionWindow(gameId, state, seat) {
  if (!eligibleDecisionSeats(gameId, state).includes(seat)) return null;
  const key = { gameId, phase: state.phase, seat, round: state.round ?? null,
    turn: state.turn ?? null, set: state.set ?? state.setNumber ?? null,
    series: state.series ?? null, leg: state.leg ?? null };
  switch (gameId) {
    case "grail":
      key.exchange = state.exchange;
      key.vessel = [state.poisonCup[seat], state.holyCup[seat]];
      key.ownConfirmed = seat === state.cupOwner
        ? state.racks.cup.trueIndex !== null : state.racks.potion.holyIndex !== null;
      key.used = state.phase === "cup" ? state.racks.cup.used
        : state.phase === "potion" ? state.racks.potion.used : [];
      break;
    case "auction": key.used = state.selected[seat]; break;
    case "fire": key.used = state.used[seat]; break;
    case "pig":
      key.used = state.used[seat]; key.attackIndex = state.attackIndex;
      key.houseSpent = state.houseSpent; key.wolfSpent = state.wolfSpent;
      break;
    case "light": key.own = seat === state.guardSetter ? state.guards : state.tourists; break;
    case "mine":
      key.setter = state.setter; key.ownReady = Boolean(state.boards[seat]);
      key.chooseCount = state.chooseCount; key.opened = state.opened[seat];
      key.skills = state.skills; key.keys = state.keys[seat];
      break;
    case "clock":
      key.hour = state.nextHour; key.minute = state.nextMinute;
      key.selected = state.selected; key.cursor = state.cursor;
      break;
    case "walker": key.bombs = state.bombs; key.cursor = state.bombCursor; break;
    case "garden": key.changeAvailable = state.changeAvailable[seat]; break;
    case "rabbit": key.traps = state.traps; break;
  }
  return JSON.stringify(key);
}
