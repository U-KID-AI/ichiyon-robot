import importlib
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


TOKEN_ENV_KEYS = (
    "BOT_INSTANCE_ID",
    "BOT_DATA_BACKEND",
    "ICHIYON_DATA_BACKEND",
    "ICHIYON_DISCORD_TOKEN",
    "IRSIA_DISCORD_TOKEN",
    "DISCORD_TOKEN",
    "DISCORD_BOT_TOKEN",
)


def load_config(env_values: Dict[str, Optional[str]]):
    original = {key: os.environ.get(key) for key in TOKEN_ENV_KEYS}
    try:
        for key in TOKEN_ENV_KEYS:
            if key in env_values:
                value = env_values[key]
                if value is None:
                    os.environ[key] = ""
                else:
                    os.environ[key] = value
            else:
                os.environ[key] = ""

        from bot import config

        return importlib.reload(config)
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check(name: str, condition: bool, detail: str, results: List[Tuple[str, bool, str]]) -> None:
    results.append((name, condition, detail))
    print("[{0}] {1} - {2}".format("OK" if condition else "NG", name, detail))


def main() -> int:
    results: List[Tuple[str, bool, str]] = []

    config = load_config(
        {
            "BOT_INSTANCE_ID": "ichiyon",
            "ICHIYON_DISCORD_TOKEN": "dummy_ichiyon",
            "IRSIA_DISCORD_TOKEN": None,
            "DISCORD_TOKEN": None,
            "DISCORD_BOT_TOKEN": None,
        }
    )
    check(
        "ichiyon token env selected",
        config.BOT_INSTANCE_ID == "ichiyon"
        and config.BOT_INSTANCE.display_name == "いちよんロボ"
        and config.TOKEN_ENV_KEY == "ICHIYON_DISCORD_TOKEN"
        and bool(config.TOKEN),
        "bot_id={0} token_env_key={1} token_present={2}".format(
            config.BOT_INSTANCE_ID,
            config.TOKEN_ENV_KEY,
            bool(config.TOKEN),
        ),
        results,
    )

    config = load_config(
        {
            "BOT_INSTANCE_ID": "ichiyon",
            "ICHIYON_DISCORD_TOKEN": None,
            "IRSIA_DISCORD_TOKEN": None,
            "DISCORD_TOKEN": "dummy_legacy",
            "DISCORD_BOT_TOKEN": None,
        }
    )
    check(
        "ichiyon legacy token fallback",
        config.BOT_INSTANCE_ID == "ichiyon"
        and config.TOKEN_ENV_KEY == "DISCORD_TOKEN"
        and config.TOKEN == "dummy_legacy",
        "bot_id={0} token_env_keys={1} token_present={2}".format(
            config.BOT_INSTANCE_ID,
            ",".join(config.TOKEN_ENV_KEYS),
            bool(config.TOKEN),
        ),
        results,
    )

    config = load_config(
        {
            "BOT_INSTANCE_ID": "ichiyon",
            "ICHIYON_DISCORD_TOKEN": None,
            "IRSIA_DISCORD_TOKEN": None,
            "DISCORD_TOKEN": None,
            "DISCORD_BOT_TOKEN": "dummy_legacy_bot",
        }
    )
    check(
        "ichiyon bot token fallback",
        config.BOT_INSTANCE_ID == "ichiyon"
        and config.TOKEN_ENV_KEY == "DISCORD_BOT_TOKEN"
        and config.TOKEN == "dummy_legacy_bot",
        "bot_id={0} token_env_key={1} token_present={2}".format(
            config.BOT_INSTANCE_ID,
            config.TOKEN_ENV_KEY,
            bool(config.TOKEN),
        ),
        results,
    )

    config = load_config(
        {
            "BOT_INSTANCE_ID": "irsia",
            "ICHIYON_DISCORD_TOKEN": "dummy_ichiyon",
            "IRSIA_DISCORD_TOKEN": "dummy_irsia",
            "DISCORD_TOKEN": "dummy_legacy",
            "DISCORD_BOT_TOKEN": None,
        }
    )
    check(
        "irsia token env selected",
        config.BOT_INSTANCE_ID == "irsia"
        and config.BOT_INSTANCE.display_name == "イルシア"
        and config.TOKEN_ENV_KEY == "IRSIA_DISCORD_TOKEN"
        and config.TOKEN == "dummy_irsia",
        "bot_id={0} token_env_key={1} token_present={2}".format(
            config.BOT_INSTANCE_ID,
            config.TOKEN_ENV_KEY,
            bool(config.TOKEN),
        ),
        results,
    )

    config = load_config(
        {
            "BOT_INSTANCE_ID": "irsia",
            "ICHIYON_DISCORD_TOKEN": "dummy_ichiyon",
            "IRSIA_DISCORD_TOKEN": None,
            "DISCORD_TOKEN": "dummy_legacy",
            "DISCORD_BOT_TOKEN": None,
        }
    )
    check(
        "irsia can fall back to common token keys",
        config.BOT_INSTANCE_ID == "irsia"
        and config.TOKEN_ENV_KEY == "ICHIYON_DISCORD_TOKEN"
        and config.TOKEN == "dummy_ichiyon",
        "bot_id={0} token_env_key={1} token_present={2}".format(
            config.BOT_INSTANCE_ID,
            config.TOKEN_ENV_KEY,
            bool(config.TOKEN),
        ),
        results,
    )

    config = load_config(
        {
            "BOT_INSTANCE_ID": "unknown",
            "ICHIYON_DISCORD_TOKEN": None,
            "IRSIA_DISCORD_TOKEN": "dummy_irsia",
            "DISCORD_TOKEN": None,
            "DISCORD_BOT_TOKEN": None,
        }
    )
    check(
        "invalid instance falls back safely",
        config.BOT_INSTANCE_ID == "ichiyon" and not config.TOKEN,
        "bot_id={0} token_present={1}".format(config.BOT_INSTANCE_ID, bool(config.TOKEN)),
        results,
    )

    config = load_config(
        {
            "BOT_INSTANCE_ID": "ichiyon",
            "BOT_DATA_BACKEND": "db",
            "ICHIYON_DATA_BACKEND": "json",
            "ICHIYON_DISCORD_TOKEN": None,
            "IRSIA_DISCORD_TOKEN": None,
            "DISCORD_TOKEN": None,
            "DISCORD_BOT_TOKEN": None,
        }
    )
    check(
        "BOT_DATA_BACKEND takes priority",
        config.DATA_BACKEND == "db",
        "data_backend={0}".format(config.DATA_BACKEND),
        results,
    )

    config = load_config(
        {
            "BOT_INSTANCE_ID": "ichiyon",
            "BOT_DATA_BACKEND": None,
            "ICHIYON_DATA_BACKEND": "db",
            "ICHIYON_DISCORD_TOKEN": None,
            "IRSIA_DISCORD_TOKEN": None,
            "DISCORD_TOKEN": None,
            "DISCORD_BOT_TOKEN": None,
        }
    )
    check(
        "ICHIYON_DATA_BACKEND remains fallback",
        config.DATA_BACKEND == "db",
        "data_backend={0}".format(config.DATA_BACKEND),
        results,
    )

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
