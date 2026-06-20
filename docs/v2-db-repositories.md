# いちよんロボ ver2.0 DBリポジトリ層

## 目的

ver2.0のPostgreSQLスキーマへアクセスするための薄いRepository層を追加します。
既存Bot挙動、既存JSON処理、管理画面実装はまだDBへ切り替えません。

## 方針

RepositoryはDB接続を受け取ってSQLを実行します。
戻り値は今後のBot/管理画面から扱いやすいように `dict` を基本にします。

SQLはパラメータ化します。
Python 3.8対応を維持するため、型ヒントは `Optional`、`List`、`Dict` などを使います。

## 追加Repository

* `MentionReactionRepository`
* `AutoReactionRepository`
* `NgWordRepository`
* `SpecialEffectRepository`
* `ModeRepository`
* `CounterRepository`
* `FeatureFlagRepository`

`MentionReactionRepository` はスキーマ上の `reaction_kind = random` を扱います。
設計上の呼び名として `random_draw` を渡された場合は `random` に正規化します。

`AutoReactionRepository` はスキーマ上の `reactions` テーブルを扱います。
特殊効果タグの付与先として `auto_reaction` を渡された場合は、スキーマ上の `reaction` に正規化します。

## 利用例

```python
from bot.db import get_connection
from bot.repositories import MentionReactionRepository

with get_connection() as connection:
    repo = MentionReactionRepository(connection)
    reactions = repo.list_reactions("123456789012345678", enabled=True)
```

更新系のRepositoryメソッドを使った場合、呼び出し側で必要なタイミングで `connection.commit()` します。

## 注意

この層は今後のver2.0実装用の土台です。
既存のJSON読み書き処理やBot起動経路からはまだ呼び出しません。
