"""Import a supplied TXT without modifying it. All outputs must remain private."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.services.llm_corpus import build_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    report = build_corpus(args.source, args.output, args.expected_count)
    report_path = args.output.with_suffix(".report.json")
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in ("count", "nonempty", "first", "last", "source_sha256", "lossless_reconstruction")}))


if __name__ == "__main__":
    main()
