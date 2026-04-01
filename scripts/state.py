#!/usr/bin/env python3
"""State persistence for skills — survives context compression.

Usage:
    # Initialize config (creates tmp/ dir, overwrites file)
    python3 scripts/state.py init <file> key=value ...

    # Set key-value pairs (updates existing keys in place)
    python3 scripts/state.py set <file> key=value ...

    # Set key-value pairs only if not already present
    python3 scripts/state.py set-default <file> key=value ...

    # Read a config or ID file (prints contents)
    python3 scripts/state.py read <file>

    # Write an ID list (one per line, overwrites)
    python3 scripts/state.py write-ids <file> <ID> [ID ...]

    # Read an ID list (prints space-separated on one line)
    python3 scripts/state.py read-ids <file>

    # Print current UTC timestamp (ISO 8601)
    python3 scripts/state.py timestamp

    # Clean tmp/ directory
    python3 scripts/state.py clean
"""
import os
import sys


def cmd_init(args):
    """Create tmp/ and write a fresh config file with key=value pairs."""
    if len(args) < 1:
        print("Usage: state.py init <file> [key=value ...]", file=sys.stderr)
        sys.exit(1)
    path = args[0]
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    pairs = _parse_pairs(args[1:])
    with open(path, "w") as f:
        for k, v in pairs:
            f.write(f"{k}: {v}\n")


def cmd_set(args):
    """Set key-value pairs, updating existing keys in place.

    Note: not atomic — assumes single-process sequential access (one
    Claude session per working directory).
    """
    if len(args) < 2:
        print("Usage: state.py set <file> key=value ...", file=sys.stderr)
        sys.exit(1)
    path = args[0]
    pairs = _parse_pairs(args[1:])
    update = {k: v for k, v in pairs}
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    # Read existing lines, update matching keys
    lines = []
    seen = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                key = line.split(":")[0].strip() if ":" in line else None
                if key in update:
                    lines.append(f"{key}: {update[key]}\n")
                    seen.add(key)
                else:
                    lines.append(line)
    # Append any new keys not already in file
    for k, v in pairs:
        if k not in seen:
            lines.append(f"{k}: {v}\n")
    with open(path, "w") as f:
        f.writelines(lines)


def cmd_set_default(args):
    """Set key-value pairs only if the key is not already present.

    Safe for cycle counters and other values that must not be reset
    if context compression causes re-entry to initialization code.
    """
    if len(args) < 2:
        print("Usage: state.py set-default <file> key=value ...", file=sys.stderr)
        sys.exit(1)
    path = args[0]
    pairs = _parse_pairs(args[1:])
    existing_keys = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if ":" in line:
                    existing_keys.add(line.split(":")[0].strip())
    new_pairs = [(k, v) for k, v in pairs if k not in existing_keys]
    if new_pairs:
        os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
        with open(path, "a") as f:
            for k, v in new_pairs:
                f.write(f"{k}: {v}\n")


def cmd_read(args):
    """Print contents of a file."""
    if len(args) < 1:
        print("Usage: state.py read <file>", file=sys.stderr)
        sys.exit(1)
    path = args[0]
    if not os.path.exists(path):
        print(f"State file not found: {path} — was it persisted in a prior step?", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        print(f.read(), end="")


def cmd_write_ids(args):
    """Write IDs to a file, one per line. Accepts zero IDs (writes empty file)."""
    if len(args) < 1:
        print("Usage: state.py write-ids <file> [ID ...]", file=sys.stderr)
        sys.exit(1)
    path = args[0]
    ids = list(dict.fromkeys(args[1:]))  # deduplicate, preserve order
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


def cmd_read_ids(args):
    """Read IDs from a file, print space-separated."""
    if len(args) < 1:
        print("Usage: state.py read-ids <file>", file=sys.stderr)
        sys.exit(1)
    path = args[0]
    if not os.path.exists(path):
        print(f"State file not found: {path} — was it persisted in a prior step?", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        ids = [line.strip() for line in f if line.strip()]
    print(" ".join(ids))


def cmd_timestamp(args):
    """Print current UTC timestamp in ISO 8601 format."""
    from datetime import datetime, timezone
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def cmd_clean(args):
    """Remove and recreate tmp/. Only call from top-level entry points."""
    import shutil
    if os.path.exists("tmp"):
        shutil.rmtree("tmp")
    os.makedirs("tmp", exist_ok=True)


def _parse_pairs(args):
    """Parse key=value arguments into (key, value) tuples."""
    pairs = []
    for arg in args:
        if "=" not in arg:
            print(f"Invalid key=value: {arg}", file=sys.stderr)
            sys.exit(1)
        k, v = arg.split("=", 1)
        pairs.append((k, v))
    return pairs


COMMANDS = {
    "init": cmd_init,
    "set": cmd_set,
    "set-default": cmd_set_default,
    "read": cmd_read,
    "write-ids": cmd_write_ids,
    "read-ids": cmd_read_ids,
    "timestamp": cmd_timestamp,
    "clean": cmd_clean,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
