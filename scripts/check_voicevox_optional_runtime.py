from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print("[{0}] {1}{2}".format(status, name, " - {0}".format(detail) if detail else ""))
    return bool(condition)


def main():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "voice-tts.md").read_text(encoding="utf-8")
    results = []
    results.append(check("voicevox service exists", "voicevox-engine:" in compose))
    results.append(check("voicevox uses explicit profile", "profiles:\n      - voicevox" in compose))
    results.append(check("voicevox does not publish host port", "50021:50021" not in compose))
    results.append(check("bot does not depend on voicevox", "condition: service_healthy\n      voicevox-engine" not in compose))
    results.append(check("voicevox health checks internal version endpoint", "/version" in compose and "127.0.0.1:50021" in compose))
    results.append(check("docs explains runtime disabled default", "TTS_RUNTIME_ENABLED=true" in docs and "false" in docs))
    results.append(check("docs explains voicevox profile", "--profile voicevox" in docs))
    ok = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok, len(results)))
    if ok != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
