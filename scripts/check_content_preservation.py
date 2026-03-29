#!/usr/bin/env python3
"""Check that content from rfe-originals/ is preserved in rfe-tasks/.

Compares original Jira snapshots to their current task files and flags
any content blocks that were dropped during revision. Content is considered
preserved if it appears in the task file OR the removed-context YAML file.

When missing blocks are found, writes them to a structured YAML file
({id}-removed-context.yaml) with type: unclassified for later semantic
classification.

Usage:
    python3 scripts/check_content_preservation.py <original> <task_file>
    python3 scripts/check_content_preservation.py --batch
    python3 scripts/check_content_preservation.py --batch --verbose
    python3 scripts/check_content_preservation.py --batch --json
"""

import argparse
import json
import os
import re
import sys

import yaml

# Add parent directory to path for artifact_utils
sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter, find_removed_context_yaml


def strip_frontmatter(content):
    """Remove YAML frontmatter from markdown content."""
    match = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
    if match:
        return content[match.end():]
    return content


def split_into_blocks(content):
    """Split markdown content into heading-delimited blocks.

    Returns list of (heading, lines) tuples. Content before the first
    heading gets heading="(preamble)".
    """
    lines = content.split('\n')
    blocks = []
    current_heading = "(preamble)"
    current_lines = []

    for line in lines:
        if re.match(r'^#{1,3}\s+', line):
            if current_lines:
                blocks.append((current_heading, current_lines))
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        blocks.append((current_heading, current_lines))

    return blocks


def get_signature_lines(lines):
    """Extract signature lines from a block — non-blank lines with 5+ words."""
    sig = []
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped.split()) >= 5:
            sig.append(normalize(stripped))
    return sig


def normalize(text):
    """Normalize whitespace for comparison."""
    return re.sub(r'\s+', ' ', text.strip().lower())


def load_removed_context_yaml(yaml_path):
    """Load existing removed-context YAML and return blocks + full target text."""
    if not yaml_path or not os.path.exists(yaml_path):
        return [], ""

    with open(yaml_path, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    if not data or 'blocks' not in data:
        return [], ""

    blocks = data['blocks'] or []
    # Build target text from all block content
    parts = []
    for block in blocks:
        content = block.get('content', '')
        if content:
            parts.append(normalize(content))
    return blocks, ' '.join(parts)


def check_preservation(original_path, task_path, yaml_path=None,
                       verbose=False):
    """Compare original to task file and find missing content blocks.

    Returns list of dicts with 'heading', 'sig_count', 'missing_count',
    'content', and optionally 'missing_lines'.
    """
    with open(original_path, encoding='utf-8') as f:
        original = strip_frontmatter(f.read())

    with open(task_path, encoding='utf-8') as f:
        task = strip_frontmatter(f.read())

    # Build the full target text (task + removed-context YAML content)
    target_text = normalize(task)
    existing_blocks, rc_text = load_removed_context_yaml(yaml_path)
    if rc_text:
        target_text += ' ' + rc_text

    original_blocks = split_into_blocks(original)
    missing = []

    for heading, lines in original_blocks:
        sig_lines = get_signature_lines(lines)
        if not sig_lines:
            continue

        found = 0
        missing_lines_list = []
        for sig in sig_lines:
            if sig in target_text:
                found += 1
            else:
                missing_lines_list.append(sig)

        preservation_rate = found / len(sig_lines)
        if preservation_rate < 0.6:
            entry = {
                'heading': heading,
                'sig_count': len(sig_lines),
                'missing_count': len(sig_lines) - found,
                'preservation_rate': round(preservation_rate, 2),
                'content': '\n'.join(lines).strip(),
            }
            if verbose:
                entry['missing_lines'] = missing_lines_list
            missing.append(entry)

    return missing


def write_removed_context_yaml(yaml_path, missing_blocks, existing_blocks=None):
    """Write or merge missing blocks into the removed-context YAML file.

    New blocks get type: unclassified. Existing blocks with a type set
    by the semantic classifier are preserved.
    """
    # Index existing blocks by heading for merge
    existing_by_heading = {}
    if existing_blocks:
        for block in existing_blocks:
            existing_by_heading[block.get('heading', '')] = block

    merged = []

    # Keep existing classified blocks
    for block in (existing_blocks or []):
        merged.append(block)

    # Add new missing blocks (not already tracked)
    for m in missing_blocks:
        heading = m['heading']
        if heading not in existing_by_heading:
            merged.append({
                'heading': heading,
                'type': 'unclassified',
                'content': m['content'],
            })

    if not merged:
        return

    data = {'blocks': merged}
    os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
    with open(yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                  sort_keys=False, width=200)


def get_yaml_path_for_task(task_path):
    """Derive the removed-context YAML path from a task file path."""
    tasks_dir = os.path.dirname(task_path)
    basename = os.path.basename(task_path).replace('.md', '')
    return os.path.join(tasks_dir, f"{basename}-removed-context.yaml")


def main():
    parser = argparse.ArgumentParser(
        description='Check content preservation between originals and tasks')
    parser.add_argument('original', nargs='?',
                        help='Path to original file')
    parser.add_argument('task_file', nargs='?',
                        help='Path to task file')
    parser.add_argument('--batch', action='store_true',
                        help='Scan all originals in artifacts/rfe-originals/')
    parser.add_argument('--verbose', action='store_true',
                        help='Show missing lines')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    parser.add_argument('--write-yaml', action='store_true',
                        help='Write missing blocks to removed-context YAML')

    args = parser.parse_args()

    if args.batch:
        originals_dir = os.path.join('artifacts', 'rfe-originals')
        tasks_dir = os.path.join('artifacts', 'rfe-tasks')

        if not os.path.isdir(originals_dir):
            print("No artifacts/rfe-originals/ directory found", file=sys.stderr)
            sys.exit(1)

        all_results = {}
        any_missing = False

        for filename in sorted(os.listdir(originals_dir)):
            if not filename.endswith('.md'):
                continue

            key = filename.replace('.md', '')
            original_path = os.path.join(originals_dir, filename)
            task_path = os.path.join(tasks_dir, filename)

            if not os.path.exists(task_path):
                if not args.json:
                    print(f"SKIP {key}: no task file")
                continue

            yaml_path = find_removed_context_yaml('artifacts', key)
            missing = check_preservation(
                original_path, task_path, yaml_path, verbose=args.verbose)

            if missing:
                any_missing = True
                all_results[key] = missing
                if not args.json:
                    print(f"FAIL {key}: {len(missing)} block(s) missing")
                    for m in missing:
                        print(f"  - {m['heading']}: "
                              f"{m['missing_count']}/{m['sig_count']} "
                              f"signature lines missing "
                              f"({m['preservation_rate']:.0%} preserved)")
                        if args.verbose and 'missing_lines' in m:
                            for line in m['missing_lines']:
                                print(f"    > {line[:100]}")

                if args.write_yaml:
                    yp = yaml_path or get_yaml_path_for_task(task_path)
                    existing, _ = load_removed_context_yaml(yaml_path)
                    write_removed_context_yaml(yp, missing, existing)
                    if not args.json:
                        print(f"       Wrote {len(missing)} block(s) to "
                              f"{os.path.basename(yp)}")
            else:
                if not args.json:
                    print(f"OK   {key}")

        if args.json:
            print(json.dumps(all_results, indent=2))

        sys.exit(1 if any_missing else 0)

    elif args.original and args.task_file:
        if not os.path.exists(args.original):
            print(f"File not found: {args.original}", file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(args.task_file):
            print(f"File not found: {args.task_file}", file=sys.stderr)
            sys.exit(1)

        # Try to find removed-context YAML
        key = os.path.basename(args.task_file).replace('.md', '')
        artifacts_dir = os.path.dirname(os.path.dirname(args.task_file))
        yaml_path = find_removed_context_yaml(artifacts_dir, key)

        missing = check_preservation(
            args.original, args.task_file, yaml_path, verbose=args.verbose)

        if args.json:
            print(json.dumps(missing, indent=2))
        elif missing:
            print(f"FAIL: {len(missing)} block(s) missing")
            for m in missing:
                print(f"  - {m['heading']}: "
                      f"{m['missing_count']}/{m['sig_count']} "
                      f"signature lines missing "
                      f"({m['preservation_rate']:.0%} preserved)")
                if args.verbose and 'missing_lines' in m:
                    for line in m['missing_lines']:
                        print(f"    > {line[:100]}")

            if args.write_yaml:
                yp = yaml_path or get_yaml_path_for_task(args.task_file)
                existing, _ = load_removed_context_yaml(yaml_path)
                write_removed_context_yaml(yp, missing, existing)
                print(f"Wrote {len(missing)} block(s) to "
                      f"{os.path.basename(yp)}")
        else:
            print("OK: all content preserved")

        sys.exit(1 if missing else 0)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
