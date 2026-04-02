#!/usr/bin/env python3
"""Generate an HTML review report from RFE review artifacts."""

import json
import os
import re
import subprocess
import sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import find_artifact_file_including_archived, read_frontmatter

ARTIFACTS = os.path.join(os.path.dirname(__file__), '..', 'artifacts')
REVIEWS_DIR = os.path.join(ARTIFACTS, 'rfe-reviews')
TASKS_DIR = os.path.join(ARTIFACTS, 'rfe-tasks')
ORIGINALS_DIR = os.path.join(ARTIFACTS, 'rfe-originals')

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

def read_removed_context(rfe_id):
    """Read removed-context YAML file if it exists."""
    path = os.path.join(TASKS_DIR, f'{rfe_id}-removed-context.yaml')
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)

def generate_diff(rfe_id):
    """Generate unified diff between original and revised RFE."""
    orig = os.path.join(ORIGINALS_DIR, f'{rfe_id}.md')
    revised = os.path.join(TASKS_DIR, f'{rfe_id}.md')
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

def badge(passed, error=None):
    if error:
        return '<span class="badge-error">ERROR</span>'
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
    args = parser.parse_args()

    rfes = []
    review_files = sorted([f for f in os.listdir(REVIEWS_DIR) if f.endswith('-review.md')])

    for rf in review_files:
        rfe_id = rf.replace('-review.md', '')
        review_fm, review_body = read_frontmatter(os.path.join(REVIEWS_DIR, rf))

        task_path = find_artifact_file_including_archived(
            os.path.dirname(TASKS_DIR), rfe_id)
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

        diff_text = generate_diff(rfe_id)
        removed_context = read_removed_context(rfe_id)

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

    # Partition into four categories
    existing = [r for r in rfes if not r['is_split_child'] and not r['is_split_parent']]
    split_parents = [r for r in rfes if r['is_split_parent']]
    intermediaries = [r for r in rfes if r.get('is_intermediary')]
    leaf_children = [r for r in rfes if r.get('is_leaf_child')]

    # Cache leaf descendants per parent
    leaves_by_parent = {}
    for sp in split_parents:
        leaves_by_parent[sp['rfe_id']] = get_leaf_descendants(sp['rfe_id'])

    n = len(rfes)
    error_count = sum(1 for r in rfes if r.get('error'))

    # Existing RFE stats (the remediation story)
    ex_errors = sum(1 for r in existing if r.get('error'))
    ex_scored = len(existing) - ex_errors
    ex_before_passing = sum(1 for r in existing if not r.get('error') and r['before_pass'])
    ex_after_passing = sum(1 for r in existing if not r.get('error') and r['after_pass'])
    ex_avg_before = sum(r['before_total'] for r in existing if not r.get('error')) / ex_scored if ex_scored else 0
    ex_avg_after = sum(r['after_total'] for r in existing if not r.get('error')) / ex_scored if ex_scored else 0

    # Split stats (leaf children only)
    sp_total_children = len(leaf_children)
    sc_errors = sum(1 for r in leaf_children if r.get('error'))
    sc_scored = len(leaf_children) - sc_errors
    sc_passing = sum(1 for r in leaf_children if not r.get('error') and r['after_pass'])
    sc_avg = sum(r['after_total'] for r in leaf_children if not r.get('error')) / sc_scored if sc_scored else 0

    removed_count = sum(1 for r in rfes if r['removed_context'])
    total_blocks = sum(len(r['removed_context'].get('blocks', [])) for r in rfes if r['removed_context'])
    genuine_blocks = sum(1 for r in rfes if r['removed_context'] for b in r['removed_context'].get('blocks', []) if b.get('type') == 'genuine')
    reworded_blocks = sum(1 for r in rfes if r['removed_context'] for b in r['removed_context'].get('blocks', []) if b.get('type') == 'reworded')

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
        font-size: 8pt;
        font-weight: 700;
        padding: 2pt 8pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .badge-fail {
        display: inline-block;
        background: #c0392b;
        color: white;
        font-size: 8pt;
        font-weight: 700;
        padding: 2pt 8pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .badge-error {
        display: inline-block;
        background: #e67e22;
        color: white;
        font-size: 8pt;
        font-weight: 700;
        padding: 2pt 8pt;
        border-radius: 3pt;
        letter-spacing: 0.5pt;
    }
    .score-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 9pt;
    }
    .score-table th {
        background: #0f3460;
        color: white;
        padding: 4pt 8pt;
        text-align: left;
        font-weight: 600;
    }
    .score-table td {
        padding: 3pt 8pt;
        border-bottom: 1px solid #eee;
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
    .summary-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 9pt;
        margin: 12pt 0;
    }
    .summary-table th {
        background: #0f3460;
        color: white;
        padding: 5pt 8pt;
        text-align: left;
    }
    .summary-table td {
        padding: 4pt 8pt;
        border-bottom: 1px solid #eee;
    }
    .summary-table tr:nth-child(even) { background: #fafafa; }
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
    @media print {
        .back-to-top { display: none; }
    }
'''

    subtitle_parts = [f'{len(existing)} existing RFEs assessed and auto-revised']
    if split_parents:
        subtitle_parts.append(f'{len(split_parents)} split into {sp_total_children} new RFEs')
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

        <h3>Existing RFEs</h3>
        <div class="summary-stats">
            <div class="stat-box">
                <div class="stat-value">{ex_before_passing}/{ex_scored}</div>
                <div class="stat-label">Passing Before</div>
            </div>
            <div class="stat-arrow">&rarr;</div>
            <div class="stat-box">
                <div class="stat-value">{ex_after_passing}/{ex_scored}</div>
                <div class="stat-label">Passing After</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{ex_avg_before:.1f}</div>
                <div class="stat-label">Avg Score Before</div>
            </div>
            <div class="stat-arrow">&rarr;</div>
            <div class="stat-box">
                <div class="stat-value">{ex_avg_after:.1f}</div>
                <div class="stat-label">Avg Score After</div>
            </div>
{f"""            <div class="stat-box" style="border-color: #e67e22;">
                <div class="stat-value" style="color: #e67e22;">{ex_errors}</div>
                <div class="stat-label">Errors</div>
            </div>""" if ex_errors else ''}
        </div>
{f"""        <h3>Split RFEs</h3>
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
        </div>""" if split_parents else ''}

        <table class="summary-table">
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

    def feasibility_text(f):
        if f == 'feasible':
            return '<span style="color:#2d6a2d;">Feasible</span>'
        if f == 'infeasible':
            return '<span style="color:#c0392b;font-weight:600;">Infeasible</span>'
        if f == 'indeterminate':
            return '<span style="color:#b8860b;font-weight:600;">Indeterminate</span>'
        return '&mdash;'

    def render_table_rows(rfe_list):
        rows = ''
        for r in rfe_list:
            d = r['after_total'] - r['before_total']
            rc = r['removed_context']
            if rc:
                blocks = rc.get('blocks', [])
                rc_text = f'{len(blocks)} block{"s" if len(blocks)!=1 else ""}'
            else:
                rc_text = '&mdash;'

            feas = feasibility_text(r.get('feasibility', ''))
            error = r.get('error')
            if error:
                rows += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a></td>
            <td colspan="4" style="background: #fef3e6;">{badge(False, error=error)} &nbsp; <span style="color: #8b4513; font-weight: 600;">{html_escape(str(error))}</span></td>
            <td>&mdash;</td>
            <td>{feas}</td>
            <td>{rc_text}</td>
        </tr>
'''
            elif r['is_split_child']:
                rows += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a></td>
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
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a></td>
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
            leaf_scored = [c for c in leaves if not c.get('error')]
            leaf_passing = sum(1 for c in leaf_scored if c['after_pass'])
            leaf_avg = sum(c['after_total'] for c in leaf_scored) / len(leaf_scored) if leaf_scored else 0
            rows += f'''        <tr>
            <td class="key-col"><a href="#{r['rfe_id']}">{html_escape(r['rfe_id'])}</a></td>
            <td>{r['before_total']}/10</td>
            <td>{badge(r['before_pass'])}</td>
            <td colspan="2" style="font-size:8pt;">&rarr; {len(leaves)} children ({leaf_passing}/{len(leaf_scored)} passing, avg {leaf_avg:.1f})</td>
            <td>&mdash;</td>
            <td>{feasibility_text(r.get('feasibility', ''))}</td>
            <td>&mdash;</td>
        </tr>
'''
        return rows

    if existing:
        html += f'''        <tr><td colspan="8" style="background:#e8eaf6;font-weight:700;font-size:9pt;padding:6pt 8pt;color:#0f3460;">Existing RFEs ({len(existing)})</td></tr>
'''
        html += render_table_rows(existing)

    if split_parents:
        html += f'''        <tr><td colspan="8" style="background:#fff3e0;font-weight:700;font-size:9pt;padding:6pt 8pt;color:#e65100;">Split RFEs ({len(split_parents)} &rarr; {sp_total_children} children)</td></tr>
'''
        html += render_split_parent_rows(split_parents)

    if leaf_children:
        html += f'''        <tr><td colspan="8" style="background:#e8f5e9;font-weight:700;font-size:9pt;padding:6pt 8pt;color:#2e7d32;">New RFEs from Splits ({len(leaf_children)})</td></tr>
'''
        html += render_table_rows(leaf_children)

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
        summary_bullets.append(f'<li><strong>Removed context:</strong> {removed_count} RFE{"s" if removed_count!=1 else ""} had content removed during revision ({total_blocks} block{"s" if total_blocks!=1 else ""} total: {reworded_blocks} reworded, {genuine_blocks} genuine implementation context preserved for strategy reference).</li>')

    html += '''            </tbody>
        </table>

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

    detail_rfes = [r for r in rfes if not r.get('error')]
    if args.revised_only:
        detail_rfes = [r for r in detail_rfes if r['auto_revised'] or r['is_split_parent'] or r.get('is_leaf_child')]

    for r in detail_rfes:
        d = r['after_total'] - r['before_total']

        html += f'''
        <div class="page">
            <h1 id="{r['rfe_id']}">{html_escape(r['rfe_id'])}</h1>
            <h2>{html_escape(r['title'])}</h2>
            <p style="margin:0 0 10pt 0;font-size:9pt;">{'Split from: <a href="#' + r['parent_key'] + '">' + html_escape(r['parent_key']) + '</a> &nbsp;|&nbsp; ' if r.get('parent_key') else ''}Technical Feasibility: {feasibility_text(r.get('feasibility', ''))}</p>
'''

        if r['is_split_parent'] or r.get('is_intermediary'):
            # Split parent or intermediary detail: tree + leaf children table
            leaves = leaves_by_parent.get(r['rfe_id'], get_leaf_descendants(r['rfe_id']))
            leaf_scored = [c for c in leaves if not c.get('error')]
            leaf_passing = sum(1 for c in leaf_scored if c['after_pass'])
            leaf_avg = sum(c['after_total'] for c in leaf_scored) / len(leaf_scored) if leaf_scored else 0

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
                        <div class="stat-label">children</div>
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
            # Render tree visualization
            def render_tree(parent_id, prefix='', is_last=True):
                tree_html = ''
                direct = children_by_parent.get(parent_id, [])
                for i, c in enumerate(direct):
                    last = (i == len(direct) - 1)
                    connector = '&#x2514;&#x2500;&#x2500; ' if last else '&#x251C;&#x2500;&#x2500; '
                    if c.get('is_intermediary'):
                        tree_html += f'<div style="white-space:pre;font-family:monospace;font-size:9pt;line-height:1.6;">{prefix}{connector}<a href="#{c["rfe_id"]}" style="color:#e65100;font-weight:600;">{html_escape(c["rfe_id"])}</a>  {html_escape(c["title"])} <span style="color:#888;font-style:italic;">(re-split)</span></div>\n'
                        child_prefix = prefix + ('    ' if last else '&#x2502;   ')
                        tree_html += render_tree(c['rfe_id'], child_prefix, last)
                    else:
                        pass_icon = '&#x2713;' if c['after_pass'] else '&#x2717;'
                        pass_color = '#2d6a2d' if c['after_pass'] else '#c0392b'
                        tree_html += f'<div style="white-space:pre;font-family:monospace;font-size:9pt;line-height:1.6;">{prefix}{connector}<a href="#{c["rfe_id"]}" style="color:#0f3460;">{html_escape(c["rfe_id"])}</a>  {html_escape(c["title"])} ({c["after_total"]}/10) <span style="color:{pass_color};font-weight:700;">{pass_icon}</span></div>\n'
                return tree_html

            html += '            <div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:6pt;padding:10pt 14pt;margin-bottom:14pt;">\n'
            # Root node
            html += f'<div style="white-space:pre;font-family:monospace;font-size:9pt;line-height:1.6;font-weight:700;">{html_escape(r["rfe_id"])}  {html_escape(r["title"])} ({r["before_total"]}/10)</div>\n'
            html += render_tree(r['rfe_id'])
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
                        <td class="key-col"><a href="#{c['rfe_id']}">{html_escape(c['rfe_id'])}</a></td>
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
</script>
</body>
</html>
'''

    output_path = args.output or os.path.join(ARTIFACTS, 'review-report.html')
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
