// In-memory sessions deliberately abort on reload. No player/world state is altered.
export class GameManager {
  constructor(games) {
    this.games = new Map(games.map(game => [game.id, game]));
    this.tables = new Map();
    this.members = new Map();
    this.sequence = 0;
    this.maxTables = 32;
  }

  join(tableId, gameId, playerId) {
    const game = this.games.get(gameId);
    if (!game || !playerId || !tableId) throw new Error("参加先が不正です。");
    if (this.members.has(playerId)) throw new Error("既に参加しています。");
    let table = this.tables.get(tableId);
    const maximum = game.maxPlayers ?? game.seats;
    if (table && (table.game !== game || table.state || table.players.length >= maximum)) {
      throw new Error("この卓には参加できません。");
    }
    if (!table) {
      if (this.tables.size >= this.maxTables) throw new Error("卓の上限です。");
      table = { id: tableId, game, players: [], state: null, token: ++this.sequence };
      this.tables.set(tableId, table);
    }
    table.players.push(playerId);
    this.members.set(playerId, tableId);
    if (!game.registration && table.players.length === maximum) {
      table.state = game.create(table.players.length);
    }
    table.token = ++this.sequence;
    return this.view(tableId);
  }

  start(tableId) {
    const table = this.tables.get(tableId);
    if (!table || table.state) return this.view(tableId);
    const minimum = table.game.minPlayers ?? table.game.seats;
    const maximum = table.game.maxPlayers ?? table.game.seats;
    if (table.players.length < minimum || table.players.length > maximum) {
      throw new Error(`開始人数は${minimum}〜${maximum}人です。`);
    }
    table.state = table.game.create(table.players.length);
    table.token = ++this.sequence;
    return this.view(tableId);
  }

  view(tableId) {
    const table = this.tables.get(tableId);
    if (!table) return null;
    return { id: table.id, gameId: table.game.id, title: table.game.title,
      players: [...table.players], token: table.token,
      state: table.state ? table.game.view(table.state) : null };
  }

  forPlayer(playerId) { return this.view(this.members.get(playerId)); }

  act(playerId, token, action, value) {
    const table = this.tables.get(this.members.get(playerId));
    if (!table?.state || table.token !== token) throw new Error("画面が古くなりました。受付から開き直してください。");
    table.state = table.game.advance(table.state, table.players.indexOf(playerId), action, value);
    table.token = ++this.sequence;
    return this.view(table.id);
  }

  close(tableId) {
    const result = this.view(tableId);
    if (!result) return null;
    this.tables.delete(tableId);
    for (const id of result.players) this.members.delete(id);
    return result;
  }

  systemAct(tableId, action, value) {
    const table = this.tables.get(tableId);
    if (!table?.state || !table.game.systemAdvance) return null;
    const before = table.state;
    table.state = table.game.systemAdvance(before, action, value);
    // Health-only events do not invalidate a pending choice form.
    if (before.phase !== table.state.phase || before.actor !== table.state.actor) table.token = ++this.sequence;
    return this.view(tableId);
  }

  leave(playerId) { return this.close(this.members.get(playerId)); }

  detach(playerId) {
    const tableId = this.members.get(playerId);
    const table = this.tables.get(tableId);
    if (!table) return null;
    const view = this.view(tableId);
    this.members.delete(playerId);
    table.token = ++this.sequence;
    return view;
  }
}
