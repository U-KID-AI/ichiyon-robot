// Per-player input windows. Timers and another player's hidden decision do not
// invalidate a simultaneous response; changing that player's legal phase does.
export function eligibleSpecialDecisions(gameId, state) {
  if (!state || state.phase === "finished") return [];
  if (gameId === "lucy" && state.phase === "success") return [];
  if (gameId === "lucy" && state.phase === "lock") {
    return [0, 1].filter(seat => state.progress[1 - seat] < 5);
  }
  if (gameId === "jinn") {
    if (state.phase === "cards") return [0, 1].filter(seat =>
      state.chosen ? !state.chosen[seat] : state.choices[seat] === null);
    if (state.phase === "settlement") return [0, 1].filter(seat => !state.lever[seat] || state.game === 3);
    return [];
  }
  if (gameId === "silence" && state.phase === "playback") return [0, 1].filter(seat => !state.guard[seat]);
  if (gameId === "sparrow") return [state.phase === "choose" ? 1 - state.setter : state.setter];
  if (["lucy", "silence", "temperance", "ace"].includes(gameId)) {
    return Number.isInteger(state.actor) ? [state.actor] : [];
  }
  return [];
}

export function specialDecisionWindow(gameId, state, seat) {
  if (!eligibleSpecialDecisions(gameId, state).includes(seat)) return null;
  const key = [gameId, state.phase, state.game ?? 0, state.round ?? 0, seat];
  if (gameId === "lucy") key.push(state.phase === "lock" ? state.progress[1 - seat] : state.remaining[seat]);
  if (gameId === "jinn") key.push(state.phase === "settlement" ? Number(state.lever[seat]) : 0);
  if (gameId === "silence") key.push(state.arranger, state.chooser, state.totalSeconds, Number(state.guard[seat]));
  if (gameId === "temperance") key.push(state.arranger, state.actor);
  if (gameId === "sparrow") key.push(state.setter);
  if (gameId === "ace") key.push(state.actor, state.rollIndex, state.currentBet, state.responderIndex ?? 0, state.tieRound ?? 0);
  return JSON.stringify(key);
}
