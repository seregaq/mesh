from __future__ import annotations

import argparse
import json

from scanner import scan
from ui import run_app



def main() -> None:
    parser = argparse.ArgumentParser(description="Mesh network manager")
    parser.add_argument("--subnet", default="192.168.199", help="Subnet prefix, e.g. 192.168.199")
    parser.add_argument("--limit", default=50, type=int, help="Last host index (exclusive)")
    parser.add_argument(
        "--mode",
        choices=["console", "gui"],
        default="gui",
        help="Run as console scanner or GUI manager",
    )
    args = parser.parse_args()

    if args.mode == "console":
        nodes = scan(subnet=args.subnet, limit=args.limit)
        print(json.dumps(nodes, indent=2, ensure_ascii=False))
        return

    run_app()


if __name__ == "__main__":
    main()
