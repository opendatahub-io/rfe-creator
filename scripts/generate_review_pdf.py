#!/usr/bin/env python3
"""Generate an HTML review report from RFE review artifacts."""

from collections import Counter
import json
import os
import re
import subprocess
import sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import find_artifact_file_including_archived, read_frontmatter

DEFAULT_ARTIFACTS = os.path.join(os.path.dirname(__file__), '..', 'artifacts')

def get_revision_history(body):
    """Extract revision history section from review body."""
    m = re.search(r'## Revision History\s*\n(.*)', body, re.DOTALL)
    return m.group(1).strip() if m else ''

def parse_before_scores(revision_history, after_scores):
    """Reconstruct before scores from revision history annotations like WHY (0->1)."""
    before = dict(after_scores)
    name_map = {
        'WHY': 'why', 'WHAT': 'what', 'HOW': 'open_to_how',
        'Open to HOW': 'open_to_how', 'Not-a-task': 'not_a_task',
        'Not a task': 'not_a_task', 'Right-sized': 'right_sized',
        'Right-sizing': 'right_sized', 'NAT': 'not_a_task',
        'RS': 'right_sized',
    }
    for match in re.finditer(r'(\w[\w\s-]*?)\s*\((\d+)(?:→|->)+(\d+)\)', revision_history):
        name = match.group(1).strip()
        before_val = int(match.group(2))
        key = name_map.get(name)
        if key:
            before[key] = before_val
    return before

def read_removed_context(rfe_id, tasks_dir):
    """Read removed-context YAML file if it exists."""
    path = os.path.join(tasks_dir, f'{rfe_id}-removed-context.yaml')
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)

def generate_diff(rfe_id, tasks_dir, originals_dir):
    """Generate unified diff between original and revised RFE."""
    orig = os.path.join(originals_dir, f'{rfe_id}.md')
    revised = os.path.join(tasks_dir, f'{rfe_id}.md')
    if not os.path.exists(orig) or not os.path.exists(revised):
        return None

    with open(revised) as f:
        revised_content = f.read()
    if revised_content.startswith('---'):
        parts = revised_content.split('---', 2)
        if len(parts) >= 3:
            revised_content = parts[2].lstrip('\n')

    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as tmp:
        tmp.write(revised_content)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ['diff', '-u', orig, tmp_path],
            capture_output=True, text=True
        )
        return result.stdout
    finally:
        os.unlink(tmp_path)

def html_escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;')

def diff_to_html(diff_text):
    if not diff_text or not diff_text.strip():
        return '<div class="no-changes">No description changes</div>'

    lines = diff_text.split('\n')
    html_parts = []
    for line in lines:
        if line.startswith('---') or line.startswith('+++'):
            continue
        if line.startswith('@@'):
            html_parts.append(f'<div class="diff-hunk">{html_escape(line)}</div>')
        elif line.startswith('+'):
            html_parts.append(f'<div class="diff-add">{html_escape(line)}</div>')
        elif line.startswith('-'):
            html_parts.append(f'<div class="diff-del">{html_escape(line)}</div>')
        elif line.startswith(' '):
            html_parts.append(f'<div class="diff-ctx">{html_escape(line)}</div>')

    return '\n'.join(html_parts)

def badge(passed, error=None, tooltip=None):
    if error:
        tip = tooltip or str(error)
        return f'<span class="badge-tip"><span class="badge-error">ERROR</span><span class="tip-text">{html_escape(tip)}</span></span>'
    if passed:
        return '<span class="badge-pass">PASS</span>'
    return '<span class="badge-fail">FAIL</span>'

def delta_class(d):
    if d > 0: return 'delta-pos'
    if d < 0: return 'delta-neg'
    return 'delta-zero'

def delta_text(d):
    if d > 0: return f'+{d}'
    return str(d)

def score_change_class(before, after):
    if after > before: return 'score-up'
    if after < before: return 'score-down'
    return 'score-same'

def score_change_text(before, after, max_val=2):
    if after > before:
        return f'{before}/{max_val} &rarr; {after}/{max_val} &#x25B2;'
    if after < before:
        return f'{before}/{max_val} &rarr; {after}/{max_val} &#x25BC;'
    return f'{after}/{max_val}'

def type_badge(block_type):
    colors = {
        'reworded': ('#6c5ce7', '#f0eeff'),
        'genuine': ('#e17055', '#fff3ef'),
        'non-substantive': ('#636e72', '#f0f0f0'),
        'unclassified': ('#d63031', '#ffeaea'),
    }
    color, bg = colors.get(block_type, ('#636e72', '#f0f0f0'))
    return f'<span style="display:inline-block;background:{bg};color:{color};font-size:8pt;font-weight:700;padding:2pt 8pt;border-radius:3pt;border:1px solid {color};letter-spacing:0.5pt;text-transform:uppercase">{html_escape(block_type)}</span>'

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate HTML review report')
    parser.add_argument('--revised-only', action='store_true',
                        help='Only include detail pages for revised RFEs '
                             '(summary table still shows all)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output file path (default: artifacts/review-report.html)')
    parser.add_argument('--artifacts-dir', type=str, default=None,
                        help='Artifacts directory (default: ../artifacts relative to script)')
    args = parser.parse_args()

    artifacts_dir = args.artifacts_dir or DEFAULT_ARTIFACTS
    reviews_dir = os.path.join(artifacts_dir, 'rfe-reviews')
    tasks_dir = os.path.join(artifacts_dir, 'rfe-tasks')
    originals_dir = os.path.join(artifacts_dir, 'rfe-originals')

    jira_server = os.environ.get('JIRA_SERVER', '').rstrip('/')

    rfes = []
    review_files = sorted([f for f in os.listdir(reviews_dir) if f.endswith('-review.md')])

    for rf in review_files:
        rfe_id = rf.replace('-review.md', '')
        review_fm, review_body = read_frontmatter(os.path.join(reviews_dir, rf))

        task_path = find_artifact_file_including_archived(
            os.path.dirname(tasks_dir), rfe_id)
        task_fm = {}
        if task_path and os.path.exists(task_path):
            task_fm, _ = read_frontmatter(task_path)
        title = task_fm.get('title', rfe_id)

        after_scores = review_fm.get('scores', {})

        # Use frontmatter before_scores if available, fall back to parsing
        revision_history = get_revision_history(review_body)
        fm_before_scores = review_fm.get('before_scores')
        if fm_before_scores:
            before_scores = fm_before_scores
        else:
            before_scores = parse_before_scores(revision_history, after_scores)

        fm_before_score = review_fm.get('before_score')
        before_total = fm_before_score if fm_before_score is not None else sum(before_scores.values())
        after_total = review_fm.get('score', sum(after_scores.values()))

        before_pass = before_total >= 7 and all(v > 0 for v in before_scores.values())
        after_pass = review_fm.get('pass', False)

        diff_text = generate_diff(rfe_id, tasks_dir, originals_dir)
        removed_context = read_removed_context(rfe_id, tasks_dir)

        error = review_fm.get('error')

        is_split_child = bool(task_fm.get('parent_key'))
        is_split_parent = (task_fm.get('status') == 'Archived'
                           and review_fm.get('recommendation') == 'split')

        rfes.append({
            'rfe_id': rfe_id,
            'title': title,
            'is_split_child': is_split_child,
            'is_split_parent': is_split_parent,
            'parent_key': task_fm.get('parent_key'),
            'before_scores': before_scores,
            'after_scores': after_scores,
            'before_total': before_total,
            'after_total': after_total,
            'before_pass': before_pass,
            'after_pass': after_pass,
            'feasibility': review_fm.get('feasibility', ''),
            'auto_revised': review_fm.get('auto_revised', False),
            'needs_attention': review_fm.get('needs_attention', False),
            'needs_attention_reason': review_fm.get('needs_attention_reason', ''),
            'recommendation': review_fm.get('recommendation', ''),
            'error': error,
            'diff_text': diff_text,
            'removed_context': removed_context,
            'revision_history': revision_history,
        })

    # Build lookup and parent->children map
    rfe_by_id = {r['rfe_id']: r for r in rfes}
    children_by_parent = {}
    for r in rfes:
        pk = r.get('parent_key')
        if pk:
            children_by_parent.setdefault(pk, []).append(r)

    # Identify intermediaries: have parent_key AND have children of their own
    for r in rfes:
        r['is_intermediary'] = r['is_split_child'] and r['rfe_id'] in children_by_parent
        r['is_leaf_child'] = r['is_split_child'] and r['rfe_id'] not in children_by_parent

    # Collect leaf descendants for a given parent (recursive tree walk)
    def get_leaf_descendants(parent_id):
        leaves = []
        for child in children_by_parent.get(parent_id, []):
            if child['is_leaf_child']:
                leaves.append(child)
            elif child['is_intermediary']:
                leaves.extend(get_leaf_descendants(child['rfe_id']))
        return leaves

    def find_tree_root(r):
        """Walk parent_key links up to the root ancestor."""
        root = r
        pk = r.get('parent_key')
        while pk:
            ancestor = rfe_by_id.get(pk)
            if not ancestor:
                break
            root = ancestor
            pk = ancestor.get('parent_key')
        return root

    # Partition into four categories
    # Note: intermediaries are by definition also split parents (is_split_child
    # AND has children), so we exclude them from split_parents to avoid
    # double-counting in the summary table.
    existing = [r for r in rfes if not r['is_split_child'] and not r['is_split_parent']]
    intermediaries = [r for r in rfes if r.get('is_intermediary')]
    intermediary_ids = {r['rfe_id'] for r in intermediaries}
    split_parents = [r for r in rfes if r['is_split_parent']
                     and r['rfe_id'] not in intermediary_ids]
    leaf_children = [r for r in rfes if r.get('is_leaf_child')]

    # Cache leaf descendants per parent
    leaves_by_parent = {}
    for sp in split_parents:
        leaves_by_parent[sp['rfe_id']] = get_leaf_descendants(sp['rfe_id'])

    # Tag children of refused/errored split parents so stats exclude them
    refused_parents = {sp['rfe_id'] for sp in split_parents if sp.get('error')}
    def _has_refused_ancestor(r):
        pk = r.get('parent_key')
        while pk:
            if pk in refused_parents:
                return True
            parent = rfe_by_id.get(pk)
            pk = parent.get('parent_key') if parent else None
        return False
    for r in rfes:
        r['parent_refused'] = (r['is_split_child'] and _has_refused_ancestor(r))

    n = len(rfes)
    error_count = sum(1 for r in rfes if r.get('error'))

    # Existing RFE stats (the remediation story)
    ex_errors = sum(1 for r in existing if r.get('error'))
    ex_scored = len(existing) - ex_errors
    ex_before_passing = sum(1 for r in existing if not r.get('error') and r['before_pass'])
    ex_after_passing = sum(1 for r in existing if not r.get('error') and r['after_pass'])
    ex_avg_before = sum(r['before_total'] for r in existing if not r.get('error')) / ex_scored if ex_scored else 0
    ex_avg_after = sum(r['after_total'] for r in existing if not r.get('error')) / ex_scored if ex_scored else 0

    # Split stats (leaf children only, excluding children of refused parents)
    submitted_leaf_children = [r for r in leaf_children if not r.get('parent_refused')]
    sp_total_children = len(submitted_leaf_children)
    sp_refused_count = len(refused_parents)
    sc_errors = sum(1 for r in submitted_leaf_children if r.get('error'))
    sc_scored = len(submitted_leaf_children) - sc_errors
    sc_passing = sum(1 for r in submitted_leaf_children if not r.get('error') and r['after_pass'])
    sc_avg = sum(r['after_total'] for r in submitted_leaf_children if not r.get('error')) / sc_scored if sc_scored else 0

    removed_count = sum(1 for r in rfes if r['removed_context'])
    total_blocks = sum(len(r['removed_context'].get('blocks', [])) for r in rfes if r['removed_context'])
    genuine_blocks = sum(1 for r in rfes if r['removed_context'] for b in r['removed_context'].get('blocks', []) if b.get('type') == 'genuine')
    reworded_blocks = sum(1 for r in rfes if r['removed_context'] for b in r['removed_context'].get('blocks', []) if b.get('type') == 'reworded')

    # Per-criterion score distributions for existing RFEs
    criterion_keys = ['what', 'why', 'open_to_how', 'not_a_task', 'right_sized']
    criterion_labels_map = {'what': 'WHAT', 'why': 'WHY', 'open_to_how': 'HOW',
                            'not_a_task': 'Task', 'right_sized': 'Scope'}
    ex_no_errors = [r for r in existing if not r.get('error')]
    criterion_dist = {}
    for key in criterion_keys:
        before_counts = Counter(r['before_scores'].get(key, 0) for r in ex_no_errors)
        after_counts = Counter(r['after_scores'].get(key, 0) for r in ex_no_errors)
        n = len(ex_no_errors) or 1
        criterion_dist[key] = {
            'before': {s: before_counts.get(s, 0) / n * 100 for s in [0, 1, 2]},
            'after': {s: after_counts.get(s, 0) / n * 100 for s in [0, 1, 2]},
        }

    # Score distribution for existing RFEs
    before_dist = Counter(r['before_total'] for r in ex_no_errors)
    after_dist = Counter(r['after_total'] for r in ex_no_errors)
    all_scores = sorted(set(before_dist.keys()) | set(after_dist.keys()))
    max_count = max(max(before_dist.values(), default=0), max(after_dist.values(), default=0), 1)

    # Auto-revision stats
    ex_auto_revised = [r for r in ex_no_errors if r.get('auto_revised')]
    ex_revised_count = len(ex_auto_revised)
    ex_revised_avg_delta = (sum(r['after_total'] - r['before_total'] for r in ex_auto_revised)
                            / ex_revised_count if ex_revised_count else 0)

    # Needs-attention counts
    ex_needs_attn = sum(1 for r in existing if r.get('needs_attention') and not r.get('error'))
    sc_needs_attn = sum(1 for r in submitted_leaf_children if r.get('needs_attention') and not r.get('error'))

    # Removed-context heading frequency
    heading_counter = Counter()
    for r in rfes:
        if r['removed_context']:
            for b in r['removed_context'].get('blocks', []):
                heading_counter[b.get('heading', 'unknown')] += 1
    top_headings = heading_counter.most_common(5)

    css = '''
    @page {
        size: letter;
        margin: 0.75in;
    }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
        font-size: 10pt;
        line-height: 1.4;
        color: #1a1a2e;
        margin: 0;
        padding: 30px 40px 40px 40px;
    }
    .page {
        page-break-after: always;
        padding-bottom: 24pt;
        margin-bottom: 24pt;
        border-bottom: 2px solid #dee2e6;
    }
    .page:last-child {
        page-break-after: avoid;
        border-bottom: none;
    }
    h1 {
        font-size: 18pt;
        color: #0f3460;
        margin: 0 0 2pt 0;
        padding-bottom: 4pt;
        border-bottom: 3px solid #e94560;
    }
    h2 {
        font-size: 12pt;
        color: #555;
        font-weight: 400;
        margin: 4pt 0 14pt 0;
    }
    h3 {
        font-size: 11pt;
        color: #0f3460;
        margin: 14pt 0 6pt 0;
    }
    .score-section { margin-bottom: 14pt; }
    .score-summary {
        display: flex;
        align-items: center;
        gap: 12pt;
        margin-bottom: 10pt;
    }
    .score-box {
        text-align: center;
        padding: 8pt 16pt;
        border-radius: 6pt;
        min-width: 80pt;
    }
    .score-box-pass {
        background: #f0f9f0;
        border: 1px solid #b8d4b8;
    }
    .score-box-fail {
        background: #fef3f3;
        border: 1px solid #e8c4c4;
    }
    .score-label {
        font-size: 8pt;
        text-transform: uppercase;
        letter-spacing: 0.5pt;
        color: #888;
        margin-bottom: 2pt;
    }
    .score-value {
        font-size: 20pt;
        font-weight: 700;
        color: #1a1a2e;
    }
    .score-result { margin-top: 2pt; }
    .score-arrow { font-size: 18pt; color: #999; }
    .score-delta {
        font-size: 16pt;
        font-weight: 700;
        padding: 4pt 8pt;
        border-radius: 4pt;
    }
    .delta-pos { color: #2d6a2d; background: #e8f5e8; }
    .delta-zero { color: #888; background: #f5f5f5; }
    .delta-neg { color: #a03030; background: #fde8e8; }
    .badge-pass {
        display: inline-block;
        background: #2d6a2d;
        color: white;
        font-size: 7pt;
        font-weight: 700;
        padding: 1pt 5pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .badge-fail {
        display: inline-block;
        background: #c0392b;
        color: white;
        font-size: 7pt;
        font-weight: 700;
        padding: 1pt 5pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .badge-error {
        display: inline-block;
        background: #c0392b;
        color: white;
        font-size: 7pt;
        font-weight: 700;
        padding: 1pt 5pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .badge-attention {
        display: inline-block;
        background: #f39c12;
        color: white;
        font-size: 7pt;
        font-weight: 700;
        padding: 1pt 5pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .badge-rejected {
        display: inline-block;
        background: #8e44ad;
        color: white;
        font-size: 7pt;
        font-weight: 700;
        padding: 1pt 5pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .badge-tip {
        position: relative;
        cursor: help;
    }
    .badge-tip .tip-text {
        visibility: hidden;
        opacity: 0;
        position: absolute;
        bottom: calc(100% + 6px);
        left: 50%;
        transform: translateX(-50%);
        background: #1a1a2e;
        color: #f0f0f0;
        font-size: 8pt;
        font-weight: 400;
        letter-spacing: 0;
        line-height: 1.4;
        padding: 6pt 10pt;
        border-radius: 4pt;
        white-space: normal;
        width: 240px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        z-index: 100;
        transition: opacity 0.15s;
        pointer-events: none;
    }
    .badge-tip .tip-text::after {
        content: '';
        position: absolute;
        top: 100%;
        left: 50%;
        transform: translateX(-50%);
        border: 5px solid transparent;
        border-top-color: #1a1a2e;
    }
    .badge-tip:hover .tip-text {
        visibility: visible;
        opacity: 1;
    }
    .score-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: 9pt;
        border-radius: 4pt;
        box-shadow: 0 2px 6px rgba(0,0,0,0.12);
        overflow: hidden;
    }
    .score-table thead tr:first-child th:first-child { border-top-left-radius: 4pt; }
    .score-table thead tr:first-child th:last-child { border-top-right-radius: 4pt; }
    .score-table th {
        background: #0f3460;
        color: white;
        padding: 4pt 13pt 4pt 13pt;
        text-align: left;
        font-weight: 600;
        border: 1px solid #0f3460;
    }
    .score-table td {
        padding: 3pt 8pt 3pt 13pt;
        border-top: 1px solid #eee;
    }
    .score-table td:first-child { border-left: 1px solid #a0b0c0; }
    .score-table td:last-child { border-right: 1px solid #a0b0c0; }
    .score-table tr:last-child td { border-bottom: 1px solid #a0b0c0; }
    .score-table th:first-child,
    .score-table td:first-child {
        width: 50%;
    }
    .score-table tr:nth-child(even) { background: #fafafa; }
    .criterion { font-weight: 600; color: #333; }
    .score-same { color: #888; }
    .score-up { color: #2d6a2d; font-weight: 600; }
    .score-down { color: #a03030; font-weight: 600; }
    .diff {
        font-family: "SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace;
        font-size: 7.5pt;
        line-height: 1.35;
        border: 1px solid #ddd;
        border-radius: 4pt;
        overflow: hidden;
    }
    .diff-hunk {
        background: #f0f0ff;
        color: #666;
        padding: 2pt 8pt;
        border-top: 1px solid #ddd;
        border-bottom: 1px solid #ddd;
        font-style: italic;
    }
    .diff-add {
        background: #e6ffec;
        color: #1a7f37;
        padding: 1pt 8pt;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .diff-del {
        background: #ffebe9;
        color: #cf222e;
        padding: 1pt 8pt;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .diff-ctx {
        background: white;
        color: #444;
        padding: 1pt 8pt;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .no-changes {
        color: #888;
        font-style: italic;
        padding: 8pt;
        text-align: center;
        border: 1px solid #eee;
        border-radius: 4pt;
        background: #fafafa;
    }
    .summary-page .subtitle {
        font-size: 11pt;
        color: #666;
        margin-top: 4pt;
    }
    .summary-stats {
        display: flex;
        align-items: center;
        gap: 12pt;
        margin: 16pt 0;
        flex-wrap: wrap;
    }
    .stat-box {
        text-align: center;
        padding: 10pt 16pt;
        background: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 6pt;
        min-width: 90pt;
    }
    .stat-value {
        font-size: 22pt;
        font-weight: 700;
        color: #0f3460;
    }
    .stat-label {
        font-size: 8pt;
        text-transform: uppercase;
        letter-spacing: 0.5pt;
        color: #888;
        margin-top: 2pt;
    }
    .stat-arrow { font-size: 18pt; color: #999; }
    .callout {
        font-size: 9pt;
        color: #555;
        margin: 8pt 0 12pt 0;
        padding: 6pt 12pt;
        background: #f0f4ff;
        border-left: 3px solid #0f3460;
        border-radius: 0 4pt 4pt 0;
    }
    .callout strong { color: #0f3460; }
    .analysis-row {
        display: flex;
        gap: 16pt;
        margin: 0 0 16pt 0;
        align-items: stretch;
    }
    .analysis-tile {
        flex: 1;
        background: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 6pt;
        padding: 12pt 16pt;
    }
    .chart-container {
        display: flex;
        align-items: stretch;
    }
    .chart-y-label {
        writing-mode: vertical-rl;
        transform: rotate(180deg);
        font-size: 7.5pt;
        font-weight: 600;
        color: #888;
        text-transform: uppercase;
        letter-spacing: 0.5pt;
        display: flex;
        align-items: center;
        justify-content: center;
        padding-right: 4pt;
    }
    .chart-x-label {
        font-size: 7.5pt;
        font-weight: 600;
        color: #888;
        text-transform: uppercase;
        letter-spacing: 0.5pt;
        text-align: center;
        margin-top: 4pt;
    }
    .criterion-area {
        flex: 1;
        position: relative;
    }
    .criterion-grid {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
    }
    .criterion-gridline {
        position: absolute;
        left: 0;
        right: 0;
        border-top: 1px solid #e0e0e0;
    }
    .criterion-grid-label {
        position: absolute;
        left: -20pt;
        font-size: 7pt;
        color: #aaa;
        transform: translateY(-50%);
    }
    .criterion-chart {
        display: flex;
        align-items: flex-end;
        gap: 0;
        justify-content: space-around;
        position: relative;
        z-index: 1;
        height: 100%;
    }
    .criterion-group {
        display: flex;
        flex-direction: column;
        align-items: center;
        flex: 1;
    }
    .criterion-bars {
        display: flex;
        gap: 3pt;
        align-items: flex-end;
    }
    .criterion-bar {
        width: 24pt;
        display: flex;
        flex-direction: column;
        justify-content: flex-end;
        border-radius: 2pt 2pt 0 0;
        overflow: hidden;
    }
    .criterion-bar-seg {
        width: 100%;
    }
    .criterion-label {
        font-size: 8pt;
        font-weight: 600;
        color: #555;
        margin-top: 4pt;
        text-align: center;
    }
    .criterion-heading {
        font-size: 9pt;
        color: #0f3460;
        font-weight: 600;
        margin: 0 0 14pt 24pt;
    }
    .histogram { font-size: 8.5pt; }
    .histogram h4 {
        font-size: 9pt;
        color: #0f3460;
        margin: 0 0 14pt 0;
        font-weight: 600;
    }
    .hist-chart-area {
        position: relative;
        margin-left: 28pt;
        padding-top: 2pt;
    }
    .hist-gridline {
        position: absolute;
        top: 0;
        bottom: 0;
        border-left: 1px solid #e0e0e0;
        z-index: 0;
    }
    .hist-x-ticks {
        display: flex;
        margin-left: 28pt;
        margin-top: 2pt;
        position: relative;
    }
    .hist-x-tick {
        position: absolute;
        font-size: 7pt;
        color: #aaa;
        transform: translateX(-50%);
    }
    .hist-row {
        display: flex;
        align-items: center;
        margin-bottom: 3pt;
        gap: 4pt;
        position: relative;
        z-index: 1;
    }
    .hist-label {
        width: 24pt;
        text-align: right;
        font-weight: 600;
        color: #555;
        font-size: 8pt;
        margin-left: -28pt;
    }
    .hist-bars {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 1pt;
    }
    .hist-bar {
        height: 10pt;
        border-radius: 2pt;
        display: flex;
        align-items: center;
        padding-left: 4pt;
        font-size: 7pt;
        font-weight: 600;
        color: white;
        min-width: 16pt;
    }
    .hist-pass-line {
        border-top: 2px dashed #5b9bd5;
        margin: 2pt 0;
        position: relative;
        z-index: 2;
    }
    .hist-pass-label {
        position: absolute;
        right: 0;
        top: -10pt;
        font-size: 7.5pt;
        font-weight: 700;
        color: #5b9bd5;
    }
    .hist-legend {
        display: flex;
        gap: 12pt;
        margin-top: 6pt;
        font-size: 7.5pt;
        color: #888;
    }
    .hist-legend-swatch {
        display: inline-block;
        width: 10pt;
        height: 10pt;
        border-radius: 2pt;
        vertical-align: middle;
        margin-right: 3pt;
    }
    .summary-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: 9pt;
        margin: 12pt 0;
        border-radius: 4pt;
        box-shadow: 0 2px 6px rgba(0,0,0,0.12);
        overflow: hidden;
    }
    .summary-table thead tr:first-child th:first-child { border-top-left-radius: 4pt; }
    .summary-table thead tr:first-child th:last-child { border-top-right-radius: 4pt; }
    .summary-table th {
        background: #0f3460;
        color: white;
        padding: 5pt 13pt 5pt 13pt;
        text-align: left;
        border: 1px solid #0f3460;
    }
    .summary-table td {
        padding: 4pt 8pt 4pt 13pt;
        border-top: 1px solid #eee;
    }
    .summary-table td:first-child { border-left: 1px solid #a0b0c0; }
    .summary-table td:last-child { border-right: 1px solid #a0b0c0; }
    .summary-table tr:last-child td { border-bottom: 1px solid #a0b0c0; }
    .summary-table tr:nth-child(even) { background: #fafafa; }
    .table-wrapper {
        position: relative;
        overflow: hidden;
        margin: 12pt 0;
    }
    .table-wrapper .summary-table { margin: 0; }
    .table-wrapper.collapsed { max-height: 400pt; }
    .table-fade {
        display: none;
        position: absolute;
        bottom: 0; left: 0; right: 0;
        height: 80pt;
        background: linear-gradient(transparent, white);
        pointer-events: none;
    }
    .table-wrapper.collapsed .table-fade { display: block; }
    .table-see-all {
        display: none;
        position: absolute;
        bottom: 16pt;
        left: 50%;
        transform: translateX(-50%);
        background: #0f3460;
        color: white;
        border: none;
        padding: 8pt 28pt;
        border-radius: 4pt;
        font-size: 10pt;
        font-weight: 600;
        cursor: pointer;
        z-index: 10;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }
    .table-see-all:hover { background: #1a4a8a; }
    .table-wrapper.collapsed .table-see-all { display: block; }
    .key-col {
        font-family: monospace;
        font-weight: 600;
        font-size: 8pt;
    }
    .key-col a {
        color: #0f3460;
        text-decoration: none;
    }
    .key-col a:hover {
        text-decoration: underline;
    }
    .revision-summary {
        margin-top: 14pt;
        padding: 10pt;
        background: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 6pt;
    }
    .revision-summary ul {
        margin: 6pt 0;
        padding-left: 16pt;
    }
    .revision-summary li { margin-bottom: 4pt; }
    .removed-context { margin-top: 14pt; }
    .removed-block {
        margin-bottom: 10pt;
        border: 1px solid #ddd;
        border-radius: 4pt;
        overflow: hidden;
    }
    .removed-block-header {
        display: flex;
        align-items: center;
        gap: 8pt;
        padding: 6pt 10pt;
        background: #f8f9fa;
        border-bottom: 1px solid #ddd;
        font-size: 9pt;
    }
    .removed-block-heading {
        font-weight: 600;
        color: #333;
    }
    .removed-block-content {
        padding: 8pt 10pt;
        font-size: 8.5pt;
        line-height: 1.4;
        color: #444;
        white-space: pre-wrap;
        word-break: break-word;
        font-family: "SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace;
        background: #fafafa;
    }
    .no-removed {
        color: #888;
        font-style: italic;
        font-size: 9pt;
    }
    .back-to-top {
        position: fixed;
        bottom: 24px;
        right: 24px;
        background: #0f3460;
        color: white;
        width: 40px;
        height: 40px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        text-decoration: none;
        font-size: 18px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        opacity: 0;
        transition: opacity 0.3s;
        z-index: 1000;
    }
    .back-to-top:hover {
        background: #e94560;
    }
    .jira-link {
        color: inherit;
        text-decoration: none;
        transition: color 0.15s;
    }
    .jira-link:hover {
        color: #e94560;
    }
    @media print {
        .back-to-top { display: none; }
    }
'''

    subtitle_parts = [f'{len(existing)} existing RFEs assessed and auto-revised']
    if split_parents:
        split_desc = f'{len(split_parents)} split into {sp_total_children} new RFEs'
        if sp_refused_count:
            split_desc += f' ({sp_refused_count} refused)'
        subtitle_parts.append(split_desc)
    if error_count:
        subtitle_parts.append(f'{error_count} error{"s" if error_count != 1 else ""}')

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{css}
</style>
</head>
<body>

    <div class="page summary-page">
        <h1>RFE Review &amp; Remediation Report</h1>
        <p class="subtitle">{", ".join(subtitle_parts)}</p>

        <h3><a href="#section-existing" class="jira-link">Existing RFEs</a></h3>
        <div class="summary-stats">
            <div class="stat-box">
                <div class="stat-value">{ex_before_passing}/{ex_scored}</div>
                <div class="stat-label">Passing Before</div>
            </div>
            <div class="stat-arrow">&rarr;</div>
            <div class="stat-box">
                <div class="stat-value">{ex_after_passing}/{ex_scored}{' <span style="color:#2d6a2d;">&#x2191;</span>' if ex_after_passing > ex_before_passing else ''}</div>
                <div class="stat-label">Passing After</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{ex_avg_before:.1f}</div>
                <div class="stat-label">Avg Score Before</div>
            </div>
            <div class="stat-arrow">&rarr;</div>
            <div class="stat-box">
                <div class="stat-value">{ex_avg_after:.1f}{' <span style="color:#2d6a2d;">&#x2191;</span>' if ex_avg_after > ex_avg_before else ''}</div>
                <div class="stat-label">Avg Score After</div>
            </div>
{f"""            <div class="stat-box" style="border-color: #e67e22;">
                <div class="stat-value" style="color: #e67e22;">{ex_errors}</div>
                <div class="stat-label">Errors</div>
            </div>""" if ex_errors else ''}\
{f"""
            <div class="stat-box" style="border-color: #f39c12;">
                <div class="stat-value" style="color: #f39c12;">{ex_needs_attn}</div>
                <div class="stat-label">Needs Attention</div>
            </div>""" if ex_needs_attn else ''}
        </div>
'''

    # Auto-revision callout
    if ex_revised_count:
        html += f'''        <div class="callout">
            <strong>Auto-revision:</strong> {ex_revised_count} of {ex_scored} existing RFEs revised, avg score improvement <span class="{"score-up" if ex_revised_avg_delta > 0 else "score-same"}">{ex_revised_avg_delta:+.1f}</span>
        </div>
'''

    # Per-criterion stacked bar chart + score distribution histogram
    # Colors: score 0 = red, score 1 = amber, score 2 = green
    seg_colors = {0: '#e05555', 1: '#f5a623', 2: '#4caf50'}
    seg_colors_muted = {0: '#f4c4c0', 1: '#fde0a8', 2: '#c8e6c0'}

    crit_area_height = 140  # pt total (bars + labels)
    crit_label_space = 18  # pt reserved for labels below bars
    crit_bar_height = crit_area_height - crit_label_space  # pt for bars

    html += f'''        <div class="analysis-row">
            <div class="analysis-tile">
                <div class="criterion-heading">Score Distribution by Criterion</div>
                <div class="chart-container">
                    <div class="chart-y-label">% of RFEs</div>
                    <div class="criterion-area" style="height:{crit_area_height}pt;margin-left:24pt;">
                        <div class="criterion-grid">
'''
    # Gridlines at 0%, 20%, 40%, 60%, 80%, 100%
    for pct in [0, 20, 40, 60, 80, 100]:
        pos = (100 - pct) / 100 * crit_bar_height
        html += f'                            <div class="criterion-gridline" style="top:{pos:.0f}pt;"></div>\n'
        html += f'                            <div class="criterion-grid-label" style="top:{pos:.0f}pt;">{pct}</div>\n'

    html += '''                        </div>
                        <div class="criterion-chart">
'''
    for key in criterion_keys:
        dist = criterion_dist[key]
        html += f'''                            <div class="criterion-group">
                                <div class="criterion-bars" style="height:{crit_bar_height}pt;">
                                    <div class="criterion-bar">
'''
        for score in [0, 1, 2]:
            pct = dist['before'][score]
            if pct > 0:
                html += f'                                        <div class="criterion-bar-seg" style="height:{pct / 100 * crit_bar_height:.1f}pt;background:{seg_colors_muted[score]};"></div>\n'
        html += '''                                    </div>
                                    <div class="criterion-bar">
'''
        for score in [0, 1, 2]:
            pct = dist['after'][score]
            if pct > 0:
                html += f'                                        <div class="criterion-bar-seg" style="height:{pct / 100 * crit_bar_height:.1f}pt;background:{seg_colors[score]};"></div>\n'
        html += f'''                                    </div>
                                </div>
                                <div class="criterion-label">{criterion_labels_map[key]}</div>
                            </div>
'''
    html += '''                        </div>
                    </div>
                </div>
                <div class="hist-legend" style="margin-top:8pt;justify-content:center;">
                    <span><span class="hist-legend-swatch" style="background:#f4c4c0;"></span>0</span>
                    <span><span class="hist-legend-swatch" style="background:#fde0a8;"></span>1</span>
                    <span><span class="hist-legend-swatch" style="background:#c8e6c0;"></span>2</span>
                    <span style="margin-left:8pt;color:#aaa;">|</span>
                    <span style="margin-left:8pt;">Before &rarr; After</span>
                </div>
            </div>
            <div class="analysis-tile histogram">
                <h4>Total Score Distribution</h4>
                <div class="chart-container">
                    <div class="chart-y-label">Total Score</div>
                    <div style="flex:1;">
'''
    def hist_color(score, muted=False):
        """Return bar color based on score: green>=7, amber 5-6, red<=4."""
        if score >= 7:
            return '#c8e6c0' if muted else '#4caf50'
        elif score >= 5:
            return '#fde0a8' if muted else '#f5a623'
        else:
            return '#f4c4c0' if muted else '#e05555'

    # Compute gridline tick positions
    tick_step = max(1, 10 ** (len(str(max_count)) - 1))
    if max_count / tick_step <= 2:
        tick_step = tick_step // 2 or 1
    hist_ticks = list(range(0, max_count + tick_step, tick_step))

    html += '                        <div class="hist-chart-area">\n'
    # Vertical gridlines
    for t in hist_ticks:
        pct = t / max_count * 100
        html += f'                            <div class="hist-gridline" style="left:{pct:.1f}%;"></div>\n'

    for s in reversed(all_scores):
        bc = before_dist.get(s, 0)
        ac = after_dist.get(s, 0)
        bw = max(bc / max_count * 100, 0)
        aw = max(ac / max_count * 100, 0)
        # Insert pass line between 7 and 6
        if s == 6 and any(x >= 7 for x in all_scores):
            html += '''                            <div class="hist-pass-line"><span class="hist-pass-label">Pass</span></div>
'''
        zero_bar = '<div class="hist-bar" style="width:12pt;min-width:12pt;background:white;border:1px solid #ccc;color:#aaa;padding-left:2pt;">0</div>'
        before_bar = f'<div class="hist-bar" style="width:{bw:.0f}%;background:{hist_color(s, muted=True)};color:#555;">{bc}</div>' if bc else zero_bar
        after_bar = f'<div class="hist-bar" style="width:{aw:.0f}%;background:{hist_color(s)};">{ac}</div>' if ac else zero_bar
        html += f'''                            <div class="hist-row">
                                <div class="hist-label">{s}</div>
                                <div class="hist-bars">
                                    {before_bar}
                                    {after_bar}
                                </div>
                            </div>
'''
    html += '                        </div>\n'

    # X-axis tick labels
    html += '                        <div class="hist-x-ticks" style="height:12pt;">\n'
    for t in hist_ticks:
        pct = t / max_count * 100
        html += f'                            <div class="hist-x-tick" style="left:{pct:.1f}%;">{t}</div>\n'
    html += '                        </div>\n'
    html += '''                        <div class="chart-x-label">Count</div>
                        <div class="hist-legend" style="margin-top:4pt;">
                            <span><span class="hist-legend-swatch" style="background:#c8e6c0;"></span>Before</span>
                            <span><span class="hist-legend-swatch" style="background:#4caf50;"></span>After</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
'''

    def feasibility_text(f):
        if f == 'feasible':
            return '<span style="color:#2d6a2d;">Feasible</span>'
        if f == 'infeasible':
            return '<span style="color:#c0392b;font-weight:600;">Infeasible</span>'
        if f == 'indeterminate':
            return '<span style="color:#b8860b;font-weight:600;">Indeterminate</span>'
        return '&mdash;'

    def jira_link(rfe_id):
        """Wrap an RFE ID in a Jira link if it's a real key and server is configured."""
        if jira_server and rfe_id.startswith('RHAIRFE-'):
            return f'<a href="{jira_server}/browse/{html_escape(rfe_id)}" target="_blank" class="jira-link" title="Open in Jira">{html_escape(rfe_id)} &#x1F517;</a>'
        return html_escape(rfe_id)

    def jira_ext(rfe_id):
        """Small external link icon for Jira keys in summary table."""
        if jira_server and rfe_id.startswith('RHAIRFE-'):
            return f' <a href="{jira_server}/browse/{html_escape(rfe_id)}" target="_blank" style="color:#0f3460;text-decoration:none;font-size:9pt;" title="Open in Jira">&#x1F517;</a>'
        return ''

    def revision_rejected(r):
        """Check if auto-revision was rejected (score decreased or explicit rejection)."""
        return (r.get('auto_revised') and not r.get('is_split_child')
                and (r.get('recommendation') == 'autorevise_reject'
                     or r['after_total'] < r['before_total']))

    def rejected_badge(r):
        if revision_rejected(r):
            return ' <span class="badge-tip"><span class="badge-rejected">REVISION REJECTED</span><span class="tip-text">Auto-revision decreased the score — original description kept, changes not submitted.</span></span>'
        return ''

    def attn_badge(r):
        if r.get('needs_attention') and not r.get('error'):
            reason = r.get('needs_attention_reason', '')
            if reason:
                return f' <span class="badge-tip"><span class="badge-attention">NEEDS ATTENTION</span><span class="tip-text">{html_escape(reason)}</span></span>'
            return ' <span class="badge-attention">NEEDS ATTENTION</span>'
        return ''

    def render_table_rows(rfe_list):
        rows = ''
        for r in rfe_list:
            d = r['after_total'] - r['before_total']
            rc = r['removed_context']
            if rc:
                blocks = rc.get('blocks', [])
                genuine = sum(1 for b in blocks if b.get('type') == 'genuine')
                rc_text = f'{len(blocks)} block{"s" if len(blocks)!=1 else ""}'
                if genuine:
                    rc_text += f' ({genuine} genuine)'
            else:
                rc_text = '&mdash;'

            feas = feasibility_text(r.get('feasibility', ''))
            error = r.get('error')
            if error:
                tip = r.get('needs_attention_reason', str(error))
                rows += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a>{jira_ext(r['rfe_id'])} {badge(False, error=error, tooltip=tip)}</td>
            <td colspan="4" style="color:#8b4513;font-size:8pt;">{html_escape(str(error))}</td>
            <td>&mdash;</td>
            <td>{feas}</td>
            <td>{rc_text}</td>
        </tr>
'''
            elif r['is_split_child']:
                refused_marker = ' <span style="color:#e67e22;font-size:7pt;font-weight:700;">(NOT SUBMITTED)</span>' if r.get('parent_refused') else ''
                rows += f'''        <tr{' style="opacity:0.6;"' if r.get('parent_refused') else ''}>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a>{jira_ext(r['rfe_id'])}{refused_marker}{attn_badge(r)}</td>
            <td>&mdash;</td>
            <td></td>
            <td>{r['after_total']}/10</td>
            <td>{badge(r['after_pass'])}</td>
            <td>&mdash;</td>
            <td>{feas}</td>
            <td>{rc_text}</td>
        </tr>
'''
            else:
                rows += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a>{jira_ext(r['rfe_id'])}{rejected_badge(r)}{attn_badge(r)}</td>
            <td>{r['before_total']}/10</td>
            <td>{badge(r['before_pass'])}</td>
            <td>{r['after_total']}/10</td>
            <td>{badge(r['after_pass'])}</td>
            <td class="{delta_class(d)}">{delta_text(d)}</td>
            <td>{feas}</td>
            <td>{rc_text}</td>
        </tr>
'''
        return rows

    def render_split_parent_rows(parent_list):
        rows = ''
        for r in parent_list:
            leaves = leaves_by_parent.get(r['rfe_id'], [])
            error = r.get('error')
            feas = feasibility_text(r.get('feasibility', ''))
            if error:
                tip = r.get('needs_attention_reason', str(error))
                rows += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a>{jira_ext(r['rfe_id'])} {badge(False, error=error, tooltip=tip)}</td>
            <td>{r['before_total']}/10</td>
            <td>{badge(r['before_pass'])}</td>
            <td colspan="2" style="font-size:8pt;color:#8b4513;font-weight:600;">&rarr; {len(leaves)} children (not submitted)</td>
            <td>&mdash;</td>
            <td>{feas}</td>
            <td>&mdash;</td>
        </tr>
'''
            else:
                leaf_scored = [c for c in leaves if not c.get('error')]
                leaf_passing = sum(1 for c in leaf_scored if c['after_pass'])
                leaf_avg = sum(c['after_total'] for c in leaf_scored) / len(leaf_scored) if leaf_scored else 0
                rows += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a>{jira_ext(r['rfe_id'])}{attn_badge(r)}</td>
            <td>{r['before_total']}/10</td>
            <td>{badge(r['before_pass'])}</td>
            <td colspan="2" style="font-size:8pt;">&rarr; {len(leaves)} children ({leaf_passing}/{len(leaf_scored)} passing, avg {leaf_avg:.1f})</td>
            <td>&mdash;</td>
            <td>{feas}</td>
            <td>&mdash;</td>
        </tr>
'''
        return rows

    TABLE_HEADER = '''        <table class="summary-table">
            <thead>
                <tr>
                    <th>RFE</th>
                    <th>Before</th>
                    <th></th>
                    <th>After</th>
                    <th></th>
                    <th>&Delta;</th>
                    <th>Technical Feasibility</th>
                    <th>Removed Content</th>
                </tr>
            </thead>
            <tbody>
'''

    # --- Existing RFEs table ---
    if existing:
        ex_collapsed = ' collapsed' if len(existing) > 10 else ''
        html += f'        <div class="table-wrapper{ex_collapsed}">\n'
        html += TABLE_HEADER
        html += f'''        <tr id="section-existing"><td colspan="8" style="background:#e8eaf6;font-weight:700;font-size:9pt;padding:6pt 8pt;color:#0f3460;">Existing RFEs ({len(existing)})</td></tr>
'''
        html += render_table_rows(existing)
        html += '            </tbody>\n        </table>\n'
        if len(existing) > 10:
            html += f'        <div class="table-fade"></div>\n'
            html += f'        <button class="table-see-all" onclick="toggleTable(this)">See all {len(existing)} RFEs</button>\n'
        html += '        </div>\n'

    # --- Split RFEs heading + stat boxes ---
    if split_parents:
        html += f"""        <h3><a href="#section-splits" class="jira-link">Split RFEs</a></h3>
        <div class="summary-stats">
            <div class="stat-box">
                <div class="stat-value">{len(split_parents)}</div>
                <div class="stat-label">RFEs Split</div>
            </div>
            <div class="stat-arrow">&rarr;</div>
            <div class="stat-box">
                <div class="stat-value">{sp_total_children}</div>
                <div class="stat-label">New RFEs Created</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{sc_passing}/{sc_scored}</div>
                <div class="stat-label">Children Passing</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{sc_avg:.1f}</div>
                <div class="stat-label">Avg Child Score</div>
            </div>
{f'''            <div class="stat-box" style="border-color: #e67e22;">
                <div class="stat-value" style="color: #e67e22;">{sp_refused_count}</div>
                <div class="stat-label">Refused</div>
            </div>''' if sp_refused_count else ''}\
{f'''
            <div class="stat-box" style="border-color: #f39c12;">
                <div class="stat-value" style="color: #f39c12;">{sc_needs_attn}</div>
                <div class="stat-label">Needs Attention</div>
            </div>''' if sc_needs_attn else ''}
        </div>
"""

    # --- Split RFEs table ---
    split_row_count = len(split_parents) + len(intermediaries) + len(leaf_children)
    if split_row_count:
        sp_collapsed = ' collapsed' if split_row_count > 10 else ''
        html += f'        <div class="table-wrapper{sp_collapsed}">\n'
        html += TABLE_HEADER

        if split_parents:
            sp_error_count = sum(1 for r in split_parents if r.get('error'))
            sp_header = f'Split RFEs ({len(split_parents)} &rarr; {sp_total_children} children'
            if sp_error_count:
                sp_header += f', {sp_error_count} refused'
            sp_header += ')'
            html += f'''        <tr id="section-splits"><td colspan="8" style="background:#fff3e0;font-weight:700;font-size:9pt;padding:6pt 8pt;color:#e65100;">{sp_header}</td></tr>
'''
            html += render_split_parent_rows(split_parents)

        if intermediaries:
            html += f'''        <tr><td colspan="8" style="background:#fff8e1;font-weight:700;font-size:9pt;padding:6pt 8pt;color:#f57f17;">Re-split Intermediaries ({len(intermediaries)}) &mdash; superseded by children</td></tr>
'''
            for r in intermediaries:
                leaves = get_leaf_descendants(r['rfe_id'])
                leaf_scored = [c for c in leaves if not c.get('error')]
                leaf_passing = sum(1 for c in leaf_scored if c['after_pass'])
                leaf_avg = sum(c['after_total'] for c in leaf_scored) / len(leaf_scored) if leaf_scored else 0
                feas = feasibility_text(r.get('feasibility', ''))
                is_refused = r.get('parent_refused')
                refused_marker = ' <span style="color:#e67e22;font-size:7pt;font-weight:700;">(CHILDREN NOT SUBMITTED)</span>' if is_refused else ''
                if is_refused:
                    html += f'''        <tr style="opacity:0.6;">
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a>{jira_ext(r['rfe_id'])}{refused_marker}</td>
            <td>&mdash;</td>
            <td></td>
            <td colspan="2" style="font-size:8pt;">superseded &rarr; {len(leaves)} children (not submitted)</td>
            <td>&mdash;</td>
            <td>{feas}</td>
            <td>&mdash;</td>
        </tr>
'''
                else:
                    html += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a>{jira_ext(r['rfe_id'])}</td>
            <td>&mdash;</td>
            <td></td>
            <td colspan="2" style="font-size:8pt;">superseded &rarr; {len(leaves)} children ({leaf_passing}/{len(leaf_scored)} passing, avg {leaf_avg:.1f})</td>
            <td>&mdash;</td>
            <td>{feas}</td>
            <td>&mdash;</td>
        </tr>
'''

        if leaf_children:
            html += f'''        <tr><td colspan="8" style="background:#e8f5e9;font-weight:700;font-size:9pt;padding:6pt 8pt;color:#2e7d32;">New RFEs from Splits ({len(leaf_children)})</td></tr>
'''
            html += render_table_rows(leaf_children)

        html += '            </tbody>\n        </table>\n'
        if split_row_count > 10:
            html += f'        <div class="table-fade"></div>\n'
            html += f'        <button class="table-see-all" onclick="toggleTable(this)">See all {split_row_count} RFEs</button>\n'
        html += '        </div>\n'

    # Build revision summary bullets dynamically
    summary_bullets = []

    # Summarize per-criterion changes (existing RFEs only — splits have no meaningful "before")
    criterion_labels = {'what': 'WHAT', 'why': 'WHY', 'open_to_how': 'HOW', 'not_a_task': 'Not-a-task', 'right_sized': 'Right-sized'}
    for key, label in criterion_labels.items():
        improved = [r for r in existing if r['before_scores'].get(key, 0) < r['after_scores'].get(key, 0)]
        degraded = [r for r in existing if r['before_scores'].get(key, 0) > r['after_scores'].get(key, 0)]
        if improved:
            ids = ', '.join(r['rfe_id'] for r in improved)
            if len(improved) == ex_scored:
                summary_bullets.append(f'<li><strong>{label}:</strong> All {ex_scored} existing RFEs improved ({improved[0]["before_scores"].get(key, 0)}&rarr;{improved[0]["after_scores"].get(key, 0)}).</li>')
            else:
                summary_bullets.append(f'<li><strong>{label} improved:</strong> {len(improved)} RFE{"s" if len(improved)!=1 else ""} ({ids}).</li>')
        if degraded:
            ids = ', '.join(r['rfe_id'] for r in degraded)
            summary_bullets.append(f'<li><strong>{label} degraded:</strong> {len(degraded)} RFE{"s" if len(degraded)!=1 else ""} ({ids}).</li>')

    # Remaining gaps — criteria still below max (existing RFEs only)
    for key, label in criterion_labels.items():
        below_max = [r for r in existing if r['after_scores'].get(key, 0) < 2]
        if below_max:
            summary_bullets.append(f'<li><strong>{label} gap:</strong> {len(below_max)} RFE{"s" if len(below_max)!=1 else ""} still below 2/2 (requires author input).</li>')

    if removed_count:
        heading_list = ', '.join(f'{h} ({c})' for h, c in top_headings) if top_headings else ''
        heading_detail = f'<br/><span style="color:#888;font-size:8.5pt;">Most common: {heading_list}</span>' if heading_list else ''
        summary_bullets.append(f'<li><strong>Removed context:</strong> {removed_count} RFE{"s" if removed_count!=1 else ""} had content removed during revision ({total_blocks} block{"s" if total_blocks!=1 else ""} total: {reworded_blocks} reworded, {genuine_blocks} genuine implementation context preserved for strategy reference).{heading_detail}</li>')

    html += '''
        <div class="revision-summary">
            <h3>Revision Summary</h3>
            <ul>
'''
    for bullet in summary_bullets:
        html += f'                {bullet}\n'
    html += '''            </ul>
        </div>
    </div>

'''

    criteria = [
        ('WHAT', 'what'),
        ('WHY', 'why'),
        ('Open to HOW', 'open_to_how'),
        ('Not a task', 'not_a_task'),
        ('Right-sized', 'right_sized'),
    ]

    # Errored split parents get detail pages (to show refusal banner + tree),
    # but other errored RFEs are excluded since there's no useful detail to show.
    detail_rfes = [r for r in rfes if not r.get('error')
                    or r['is_split_parent']]
    if args.revised_only:
        detail_rfes = [r for r in detail_rfes if r['auto_revised'] or r['is_split_parent'] or r.get('is_leaf_child')]

    def render_tree(parent_id, prefix='', is_last=True, highlight_id=None):
        tree_html = ''
        direct = children_by_parent.get(parent_id, [])
        for i, c in enumerate(direct):
            last = (i == len(direct) - 1)
            connector = '&#x2514;&#x2500;&#x2500; ' if last else '&#x251C;&#x2500;&#x2500; '
            is_highlighted = (c['rfe_id'] == highlight_id)
            hl_start = '<span style="background:#fff3cd;padding:1pt 4pt;border-radius:3pt;">' if is_highlighted else ''
            hl_end = ' &#x25C0;</span>' if is_highlighted else ''
            if c.get('is_intermediary'):
                tree_html += f'<div style="white-space:pre;font-family:monospace;font-size:9pt;line-height:1.6;">{prefix}{connector}{hl_start}<a href="#{c["rfe_id"]}" style="color:#e65100;font-weight:600;">{html_escape(c["rfe_id"])}</a>  {html_escape(c["title"])} <span style="color:#888;font-style:italic;">(re-split)</span>{hl_end}</div>\n'
                child_prefix = prefix + ('    ' if last else '&#x2502;   ')
                tree_html += render_tree(c['rfe_id'], child_prefix, last, highlight_id)
            else:
                if c.get('parent_refused'):
                    score_style = 'color:#999;font-style:italic;'
                    suffix = ' not submitted'
                elif c['after_pass']:
                    score_style = 'color:#2d6a2d;font-weight:700;'
                    suffix = ''
                else:
                    score_style = 'color:#c0392b;font-weight:700;'
                    suffix = ''
                tree_html += f'<div style="white-space:pre;font-family:monospace;font-size:9pt;line-height:1.6;">{prefix}{connector}{hl_start}<a href="#{c["rfe_id"]}" style="color:#0f3460;">{html_escape(c["rfe_id"])}</a> <span style="{score_style}">[{c["after_total"]}/10]{suffix}</span>  {html_escape(c["title"])}{hl_end}</div>\n'
        return tree_html

    for r in detail_rfes:
        d = r['after_total'] - r['before_total']

        html += f'''
        <div class="page">
            <h1 id="{r['rfe_id']}">{jira_link(r['rfe_id'])}</h1>
            <h2>{html_escape(r['title'])}</h2>
            <p style="margin:0 0 10pt 0;font-size:9pt;">{'Split from: <a href="#' + r['parent_key'] + '">' + html_escape(r['parent_key']) + '</a> &nbsp;|&nbsp; ' if r.get('parent_key') else ''}Technical Feasibility: {feasibility_text(r.get('feasibility', ''))}</p>
'''
        if r.get('parent_refused'):
            # Find the refused ancestor by walking up the tree
            refused_ancestor = {}
            node = r
            while node.get('parent_key'):
                parent = rfe_by_id.get(node['parent_key'], {})
                if parent.get('rfe_id') in refused_parents:
                    refused_ancestor = parent
                    break
                node = parent
            parent_reason = refused_ancestor.get('needs_attention_reason', refused_ancestor.get('error', ''))
            html += f'''
            <div style="background:#fef3e6;border:2px solid #e67e22;border-radius:6pt;padding:10pt 14pt;margin-bottom:14pt;">
                <div style="font-size:10pt;font-weight:700;color:#e67e22;">&#x26A0; Not Submitted</div>
                <div style="font-size:9pt;color:#8b4513;margin-top:4pt;">Parent <a href="#{html_escape(refused_ancestor.get('rfe_id', ''))}" style="color:#8b4513;font-weight:600;">{html_escape(refused_ancestor.get('rfe_id', ''))}</a> split was refused: {html_escape(str(parent_reason))}</div>
            </div>
'''
        elif r.get('is_intermediary'):
            html += f'''
            <div style="background:#fff8e1;border:2px solid #f57f17;border-radius:6pt;padding:10pt 14pt;margin-bottom:14pt;">
                <div style="font-size:10pt;font-weight:700;color:#f57f17;">Superseded &mdash; Re-split into children below</div>
                <div style="font-size:9pt;color:#8b6914;margin-top:4pt;">This RFE was not submitted to Jira. It was further decomposed and its children were submitted instead.</div>
            </div>
'''
        if r.get('needs_attention') and not r.get('error') and not r.get('parent_refused') and not r.get('is_intermediary'):
            attn_reason = r.get('needs_attention_reason', '')
            html += f'''
            <div style="background:#fef9e6;border:2px solid #f39c12;border-radius:6pt;padding:10pt 14pt;margin-bottom:14pt;">
                <div style="font-size:10pt;font-weight:700;color:#f39c12;">&#x26A0; Needs Attention</div>
                {f'<div style="font-size:9pt;color:#8b6914;margin-top:4pt;">{html_escape(attn_reason)}</div>' if attn_reason else ''}
            </div>
'''
        if revision_rejected(r):
            html += '''
            <div style="background:#f3e8ff;border:2px solid #8e44ad;border-radius:6pt;padding:10pt 14pt;margin-bottom:14pt;">
                <div style="font-size:10pt;font-weight:700;color:#8e44ad;">Auto-revision Rejected</div>
                <div style="font-size:9pt;color:#5b2c6f;margin-top:4pt;">Auto-revision decreased the score &mdash; original description kept, changes not submitted to Jira.</div>
            </div>
'''

        if r['is_split_parent'] or r.get('is_intermediary'):
            # Split parent or intermediary detail: tree + leaf children table
            leaves = leaves_by_parent.get(r['rfe_id'], get_leaf_descendants(r['rfe_id']))
            leaf_scored = [c for c in leaves if not c.get('error')]
            leaf_passing = sum(1 for c in leaf_scored if c['after_pass'])
            leaf_avg = sum(c['after_total'] for c in leaf_scored) / len(leaf_scored) if leaf_scored else 0
            split_error = r.get('error')

            if split_error:
                attn_reason = r.get('needs_attention_reason', '')
                reason_text = f': {html_escape(attn_reason)}' if attn_reason else ''
                html += f'''
            <div style="background:#fef3e6;border:2px solid #e67e22;border-radius:6pt;padding:12pt 16pt;margin-bottom:14pt;">
                <div style="font-size:11pt;font-weight:700;color:#e67e22;margin-bottom:4pt;">&#x26A0; Split Refused &mdash; Not Submitted</div>
                <div style="font-size:9pt;color:#8b4513;">{html_escape(str(split_error))}{reason_text}</div>
            </div>
'''

            html += f'''
            <div class="score-section">
                <div class="score-summary">
                    <div class="score-box {'score-box-pass' if r['before_pass'] else 'score-box-fail'}">
                        <div class="score-label">Original Score</div>
                        <div class="score-value">{r['before_total']}/10</div>
                        <div class="score-result">{badge(r['before_pass'])}</div>
                    </div>
                    <div class="score-arrow">&rarr;</div>
                    <div class="stat-box">
                        <div class="stat-label">Split Into</div>
                        <div class="stat-value">{len(leaves)}</div>
                        <div class="stat-label">children{' (not submitted)' if split_error else ''}</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-label">Children Passing</div>
                        <div class="stat-value">{leaf_passing}/{len(leaf_scored)}</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-label">Avg Child Score</div>
                        <div class="stat-value">{leaf_avg:.1f}</div>
                    </div>
                </div>
            </div>

            <h3>Split Tree</h3>
'''
            # For intermediaries, show the full tree from the root ancestor
            tree_root = find_tree_root(r) if r.get('is_intermediary') else r
            highlight_id = r['rfe_id']
            root_hl = (tree_root['rfe_id'] == highlight_id)
            hl_start = '<span style="background:#fff3cd;padding:1pt 4pt;border-radius:3pt;">' if root_hl else ''
            hl_end = ' &#x25C0;</span>' if root_hl else ''
            html += '            <div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:6pt;padding:10pt 14pt;margin-bottom:14pt;">\n'
            # Root node
            html += f'<div style="white-space:pre;font-family:monospace;font-size:9pt;line-height:1.6;font-weight:700;">{hl_start}<a href="#{tree_root["rfe_id"]}" style="color:inherit;">{html_escape(tree_root["rfe_id"])}</a>  {html_escape(tree_root["title"])} ({tree_root["before_total"]}/10){hl_end}</div>\n'
            html += render_tree(tree_root['rfe_id'], highlight_id=highlight_id)
            html += '            </div>\n'

            html += '''
            <h3>Children</h3>
            <table class="summary-table">
                <thead>
                    <tr>
                        <th>RFE</th>
                        <th>Title</th>
                        <th>Score</th>
                        <th></th>
                        <th>Technical Feasibility</th>
                    </tr>
                </thead>
                <tbody>
'''
            for c in leaves:
                html += f'''                    <tr>
                        <td class="key-col"><a href="#{c['rfe_id']}">{html_escape(c['rfe_id'])}</a>{jira_ext(c['rfe_id'])}</td>
                        <td>{html_escape(c['title'])}</td>
                        <td>{c['after_total']}/10</td>
                        <td>{badge(c['after_pass'])}</td>
                        <td>{feasibility_text(c.get('feasibility', ''))}</td>
                    </tr>
'''
            html += '''                </tbody>
            </table>
'''
        elif r.get('is_leaf_child'):
            # Split child detail: score only, no before
            html += f'''
            <div class="score-section">
                <div class="score-summary">
                    <div class="score-box {'score-box-pass' if r['after_pass'] else 'score-box-fail'}">
                        <div class="score-label">Score</div>
                        <div class="score-value">{r['after_total']}/10</div>
                        <div class="score-result">{badge(r['after_pass'])}</div>
                    </div>
                </div>

                <table class="score-table">
                    <thead>
                        <tr>
                            <th>Criterion</th>
                            <th>Score</th>
                        </tr>
                    </thead>
                    <tbody>
'''
            for crit_name, crit_key in criteria:
                av = r['after_scores'].get(crit_key, 0)
                zero_marker = ' <span style="color:#c0392b;font-weight:700;" title="Auto-fail: 0/2">&#x2716;</span>' if av == 0 else ''
                html += f'''                        <tr>
                            <td class="criterion">{crit_name}</td>
                            <td>{av}/2{zero_marker}</td>
                        </tr>
'''

            html += '''                    </tbody>
                </table>
            </div>

'''
            # Show full split tree with this child highlighted
            tree_root = find_tree_root(r)
            if tree_root['rfe_id'] != r['rfe_id']:
                html += '            <h3>Split Tree</h3>\n'
                html += '            <div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:6pt;padding:10pt 14pt;margin-bottom:14pt;">\n'
                html += f'<div style="white-space:pre;font-family:monospace;font-size:9pt;line-height:1.6;font-weight:700;"><a href="#{tree_root["rfe_id"]}" style="color:inherit;">{html_escape(tree_root["rfe_id"])}</a>  {html_escape(tree_root["title"])} ({tree_root["before_total"]}/10)</div>\n'
                html += render_tree(tree_root['rfe_id'], highlight_id=r['rfe_id'])
                html += '            </div>\n'

        else:
            # Regular detail: before/after scores
            html += f'''
            <div class="score-section">
                <div class="score-summary">
                    <div class="score-box {'score-box-pass' if r['before_pass'] else 'score-box-fail'}">
                        <div class="score-label">Before</div>
                        <div class="score-value">{r['before_total']}/10</div>
                        <div class="score-result">{badge(r['before_pass'])}</div>
                    </div>
                    <div class="score-arrow">&rarr;</div>
                    <div class="score-box {'score-box-pass' if r['after_pass'] else 'score-box-fail'}">
                        <div class="score-label">After</div>
                        <div class="score-value">{r['after_total']}/10</div>
                        <div class="score-result">{badge(r['after_pass'])}</div>
                    </div>
                    <div class="score-delta {delta_class(d)}">{delta_text(d)}</div>
                </div>

                <table class="score-table">
                    <thead>
                        <tr>
                            <th>Criterion</th>
                            <th>Before</th>
                            <th>After</th>
                            <th>Change</th>
                        </tr>
                    </thead>
                    <tbody>
'''
            for crit_name, crit_key in criteria:
                bv = r['before_scores'].get(crit_key, 0)
                av = r['after_scores'].get(crit_key, 0)
                zero_marker = ' <span style="color:#c0392b;font-weight:700;" title="Auto-fail: 0/2">&#x2716;</span>' if av == 0 else ''
                html += f'''                        <tr>
                            <td class="criterion">{crit_name}</td>
                            <td>{bv}/2</td>
                            <td>{av}/2</td>
                            <td class="{score_change_class(bv, av)}">{score_change_text(bv, av)}{zero_marker}</td>
                        </tr>
'''

            html += '''                    </tbody>
                </table>
            </div>

'''
            html += '            <h3>Description Changes</h3>\n'
            html += '            <div class="diff">\n'
            html += diff_to_html(r['diff_text'])
            html += '\n            </div>\n'

            rc = r['removed_context']
            if rc and rc.get('blocks'):
                html += '\n            <h3>Removed Context</h3>\n'
                html += '            <div class="removed-context">\n'
                for block in rc['blocks']:
                    heading = block.get('heading', '(untitled)')
                    btype = block.get('type', 'unclassified')
                    content = block.get('content', '')
                    html += f'''                <div class="removed-block">
                    <div class="removed-block-header">
                        <span class="removed-block-heading">{html_escape(heading)}</span>
                        {type_badge(btype)}
                    </div>
                    <div class="removed-block-content">{html_escape(content)}</div>
                </div>
'''
                html += '            </div>\n'

        html += '        </div>\n'

    html += '''
<a href="#" class="back-to-top" id="backToTop" title="Back to top">&#x25B2;</a>
<script>
var btn = document.getElementById('backToTop');
window.addEventListener('scroll', function() {
    btn.style.opacity = window.scrollY > 300 ? '1' : '0';
    btn.style.pointerEvents = window.scrollY > 300 ? 'auto' : 'none';
});
function toggleTable(el) {
    var w = el.closest('.table-wrapper');
    w.classList.remove('collapsed');
}
</script>
</body>
</html>
'''

    output_path = args.output or os.path.join(artifacts_dir, 'review-report.html')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(html)
    print(f'Report written to {output_path}')
    print(f'{n} RFEs ({len(existing)} existing, {len(split_parents)} split parents, {len(leaf_children)} leaf children, {len(intermediaries)} intermediaries)')
    if ex_scored:
        print(f'Existing: {ex_before_passing}/{ex_scored} passing before, {ex_after_passing}/{ex_scored} passing after, avg {ex_avg_before:.1f} -> {ex_avg_after:.1f}')
    if split_parents:
        print(f'Splits: {len(split_parents)} RFEs split into {sp_total_children} children, {sc_passing}/{sc_scored} children passing, avg {sc_avg:.1f}')
    if error_count:
        print(f'{error_count} error{"s" if error_count != 1 else ""}')
    if removed_count:
        print(f'{removed_count} RFEs with removed context ({total_blocks} blocks)')

if __name__ == '__main__':
    main()
