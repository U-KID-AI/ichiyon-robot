"""Offline native Molcar carrot boost regression entry point."""
from pathlib import Path
import subprocess


if __name__ == "__main__":
    subprocess.run(["node", str(Path(__file__).with_suffix(".mjs"))], check=True)
