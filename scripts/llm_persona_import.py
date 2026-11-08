"""Validate and atomically install privately supplied persona material."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.services.llm_persona import Persona


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("/app/secrets/llm_persona.json"))
    args = parser.parse_args()
    persona = Persona.load(args.source)
    if not args.source.is_file():
        parser.error("source file does not exist")
    data = json.loads(args.source.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as target:
        os.chmod(target.name, 0o600)
        json.dump(data, target, ensure_ascii=False)
        target.flush()
        os.fsync(target.fileno())
    os.replace(target.name, args.output)
    print(json.dumps({"accepted_examples": len(persona.examples), "guide_present": bool(persona.guide)}))


if __name__ == "__main__":
    main()
