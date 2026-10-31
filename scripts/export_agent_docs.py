"""Write the documentation chunks the assistant searches to a JSON file, for apps that don't
have this repository's docs (the portfolio vendors the file as tfl_live/agent_docs.json).

    python scripts/export_agent_docs.py ../joel_portfolio/tfl_live/agent_docs.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.docs_search import load_chunks  # noqa: E402

if __name__ == "__main__":
    chunks = load_chunks()
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=0)
    print(f"{len(chunks)} chunks written to {sys.argv[1]}")
