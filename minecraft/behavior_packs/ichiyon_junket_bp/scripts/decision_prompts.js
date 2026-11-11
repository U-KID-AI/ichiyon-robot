// Server-owned, per-player decision dispatch. Closing a form never causes an
// unbounded reopen loop; the wall panel remains an explicit resume action.
export function createDecisionPrompts({ world, system, read, eligible, open,
  busy = () => false, key = current => String(current.token), notify = null }) {
  const records = new Map();
  let generation = 0;
  function online(id) { return world.getAllPlayers().find(player => player.id === id); }
  function schedule(current = read()) {
    if (!current?.state || current.state.phase === "finished") return;
    const seats = eligible(current) ?? [];
    for (const seat of seats) {
      const id = current.players[seat];
      if (!id || !online(id)) continue;
      const stamp = `${current.id}:${key(current, seat)}`;
      if (records.get(id)?.stamp === stamp) continue;
      const record = { stamp, generation, attempts: 0, handle: null };
      const previous = records.get(id);
      if (previous?.handle !== null && previous?.handle !== undefined) system.clearRun?.(previous.handle);
      records.set(id, record);
      const run = async () => {
        record.handle = null;
        if (record.generation !== generation || records.get(id) !== record) return;
        const live = read(), player = online(id);
        if (!player || !live?.state || live.players[seat] !== id ||
            !(eligible(live) ?? []).includes(seat) ||
            `${live.id}:${key(live, seat)}` !== stamp) { schedule(live); return; }
        if (busy(id)) {
          if (++record.attempts <= 3) record.handle = system.runTimeout(run, 10);
          else if (records.get(id) === record) records.delete(id);
          return;
        }
        try { await open(player, live); }
        catch (error) { notify?.(player, `操作画面を開けませんでした。ゲーム操作パネルから再開できます。${error?.message ?? error}`); }
        // An accepted answer may have opened another decision window while the
        // old callback was busy. Reconcile once, after its promise has settled.
        schedule(read());
      };
      record.handle = system.runTimeout(run, 4);
    }
  }
  function forget(id) {
    const record = records.get(id);
    if (record?.handle !== null && record?.handle !== undefined) system.clearRun?.(record.handle);
    records.delete(id);
  }
  return { schedule, forget, clear() { generation++; for (const id of records.keys()) forget(id); } };
}
