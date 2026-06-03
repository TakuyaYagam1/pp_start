from __future__ import annotations

import re
import sys
from pathlib import Path


TARGET_RE = re.compile(r"^([a-zA-Z0-9_.-]+):.*##\s?(.*)$")
GROUP_RE = re.compile(r"^##@\s?(.*)$")


def main(paths: list[str]) -> None:
    print("\nUsage:\n  make \033[36m<target>\033[0m")
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue

        for line in path.read_text(encoding="utf-8").splitlines():
            group_match = GROUP_RE.match(line)
            if group_match is not None:
                print(f"\n\033[1m{group_match.group(1)}\033[0m")
                continue

            target_match = TARGET_RE.match(line)
            if target_match is not None:
                print(
                    f"  \033[36m{target_match.group(1):<24}\033[0m "
                    f"{target_match.group(2)}"
                )


if __name__ == "__main__":
    main(sys.argv[1:])
