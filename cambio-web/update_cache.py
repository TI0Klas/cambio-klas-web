from __future__ import annotations

import sys

from backend import refresh_cache


def main() -> None:
    force = "--force" in sys.argv
    payload = refresh_cache(force=force)
    print(payload.get("updated_at") or "cache updated")
    if payload.get("error"):
        print(f'warning: {payload["error"]}')


if __name__ == "__main__":
    main()
