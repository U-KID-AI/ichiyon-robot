import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.services.voice.ducking import DuckingConfig, DuckingEnvelope


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    disabled = DuckingEnvelope(DuckingConfig(enabled=False, music_gain=0.5, attack_ms=100, release_ms=300))
    disabled.set_tts_active(True)
    results.append(check("disabled keeps gain", disabled.step() == 1.0))

    instant = DuckingEnvelope(DuckingConfig(enabled=True, music_gain=0.5, attack_ms=0, release_ms=0))
    instant.set_tts_active(True)
    results.append(check("instant attack", instant.step() == 0.5))
    instant.set_tts_active(False)
    results.append(check("instant release", instant.step() == 1.0))

    smooth = DuckingEnvelope(DuckingConfig(enabled=True, music_gain=0.5, attack_ms=100, release_ms=300))
    smooth.set_tts_active(True)
    values = [smooth.step() for _ in range(4)]
    results.append(check("smooth attack decreases", values[0] > values[-1] >= 0.5))
    smooth.set_tts_active(False)
    release_values = [smooth.step() for _ in range(4)]
    results.append(check("smooth release increases", release_values[0] < release_values[-1] <= 1.0))
    smooth.reset()
    results.append(check("reset returns full gain", smooth.step() == 1.0))

    clamped = DuckingConfig(enabled=True, music_gain=-1.0, attack_ms=-10, release_ms=-10)
    env = DuckingEnvelope(clamped)
    env.set_tts_active(True)
    results.append(check("config clamp lower", env.step() == 0.0))
    clamped_high = DuckingConfig(enabled=True, music_gain=2.0, attack_ms=0, release_ms=0)
    high_env = DuckingEnvelope(clamped_high)
    high_env.set_tts_active(True)
    results.append(check("config clamp upper", high_env.step() == 1.0))

    print("voice ducking checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
