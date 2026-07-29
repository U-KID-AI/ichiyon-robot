from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print("[{0}] {1}{2}".format(status, name, " - {0}".format(detail) if detail else ""))
    return bool(condition)


def main():
    external = (ROOT / "bot" / "services" / "external_http.py").read_text(encoding="utf-8")
    jma = (ROOT / "bot" / "services" / "jma_weather.py").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "operations" / "external-http-policy.md").read_text(encoding="utf-8")
    results = []
    results.append(check("policy defines timeout fields", "connect_timeout" in external and "read_timeout" in external))
    results.append(check("policy handles retry-after", "retry-after" in external.lower()))
    results.append(check("policy limits retryable statuses", "status_code == 429" in external and "500 <= status_code <= 599" in external))
    results.append(check("policy disables trust_env by default", "trust_env: bool = False" in external))
    results.append(check("policy has redacted URL helper", "redacted_url_for_log" in external and "query" not in external.split("def redacted_url_for_log", 1)[1].split("async def", 1)[0]))
    results.append(check("JMA uses external policy", "ExternalHttpPolicy" in jma and "fetch_external_json" in jma))
    results.append(check("JMA no longer imports httpx directly", "import httpx" not in jma))
    results.append(check("docs describe provider migration", "JMA天気取得" in docs and "今回移行しないProvider" in docs))
    ok = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok, len(results)))
    if ok != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
