#!/usr/bin/env python3
"""Send OSC messages to the running visualizer.

Examples:
    python osc_send.py /bpm 128
    python osc_send.py /bounce 1 /bounce_amount 0.15
    python osc_send.py /preset rave
    python osc_send.py --port 9001 /cycle 1 /decay 0.9
    python osc_send.py /tap

Common addresses (any parameter name from control.SPEC also works):
    /bpm /bounce /bounce_amount /bounce_decay /displace /disp_x /disp_y
    /decay /zoom /hue /hue_speed /kaleido /density /spiral /mode
    commands: /tap  /reset  /preset <name>  /preset/save <name>
              /random 0|1  /cycle 0|1  /spiral 0|1  /mode spiral|collage
"""

from __future__ import annotations

import argparse
import sys


def coerce(token: str):
    low = token.lower()
    if low in ("true", "on"):
        return 1.0
    if low in ("false", "off"):
        return 0.0
    try:
        return float(token)
    except ValueError:
        return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("messages", nargs="+", help="pairs of <address> <value>, or a single address for commands")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args(argv)

    try:
        from pythonosc.udp_client import SimpleUDPClient
    except Exception:
        print("python-osc not installed:  pip install python-osc", file=sys.stderr)
        return 1

    client = SimpleUDPClient(args.host, args.port)
    toks = args.messages
    i = 0
    while i < len(toks):
        addr = toks[i]
        if not addr.startswith("/"):
            print(f"skipping '{addr}' (addresses start with /)", file=sys.stderr)
            i += 1
            continue
        # command addresses take no value
        cmd = addr.strip("/").split("/")[0] in ("tap", "reset")
        if cmd or i + 1 >= len(toks) or toks[i + 1].startswith("/"):
            client.send_message(addr, [])
            print(f"sent {addr}")
            i += 1
        else:
            value = coerce(toks[i + 1])
            client.send_message(addr, value)
            print(f"sent {addr} {value}")
            i += 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
