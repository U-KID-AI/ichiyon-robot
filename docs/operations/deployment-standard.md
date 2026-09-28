# 安全なデプロイ標準手順

## 禁止事項

- `git reset --hard`
- `git clean`
- `docker compose down`
- volume削除
- stash apply/pop/drop/clear
- `.env` やsecretsの表示
- `docker-compose.yml` の無条件上書き

## 事前確認

1. `git rev-parse HEAD`
2. `git status --short`
3. `git diff --stat -- docker-compose.yml`
4. `git stash list`
5. `docker compose ps`
6. `docker volume ls`
7. memory / disk

production app OCIのimmutable deploymentでは、[storage capacity](storage-capacity.md) の容量判定を必須とする。`df`の割合だけで進行を決めず、staging/build前とapp停止直前にavailable bytes、inode、停止後backupとrollbackの必要量を測定する。不足または測定不能ならappを停止しない。`DEPLOY_ERROR=INSUFFICIENT_STORAGE` を通常の一時的な失敗として無条件に繰り返さない。

容量guard自体を初めて導入するときも、正式なdeploy経路を始める直前にread-onlyで同じ必要量を再計算する。過去の調査値は流用しない。十分なheadroomが証明できる場合だけexact merge SHAを配備し、不足・不明ならproductionを変更せず人の判断を待つ。容量確保のためのprune、backup/release/log削除はこの手順に含めない。

## 更新方針

- `origin/main` を取得する。
- 本番 `docker-compose.yml` にtracked差分がある場合、先にbackupを作る。
- compose差分がある場合は三者確認してから統合する。
- 通常recreateは `bot`, `bot-irsia`。
- admin変更時のみ `admin` を含める。
- `db`, `youtube-vpn-proxy`, `voicevox-engine` は通常recreateしない。

## 確認

- HEADがorigin/mainと一致すること。
- `bot`, `bot-irsia`, `admin` がrunningでrestart countが増えていないこと。
- `db` と `youtube-vpn-proxy` がhealthyであること。
- `voicevox-engine` はTTS停止中の通常運用では停止のまま。
- 直近ログに新規Traceback / ERRORがないこと。

## rollback判断材料

- 更新前HEAD
- 更新後HEAD
- recreate対象サービス
- restart count
- 直近ログ
- Compose差分stat
- memory / disk

この手順書は自動rollbackを実行しない。判断材料を表示し、復旧が必要な場合は別途明示承認を得る。
