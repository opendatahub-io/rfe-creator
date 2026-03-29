#!/usr/bin/env python3
"""Generate an HTML review report from RFE review artifacts."""

import json
import os
import re
import subprocess
import sys
import yaml

ARTIFACTS = os.path.join(os.path.dirname(__file__), '..', 'artifacts')
REVIEWS_DIR = os.path.join(ARTIFACTS, 'rfe-reviews')
TASKS_DIR = os.path.join(ARTIFACTS, 'rfe-tasks')
ORIGINALS_DIR = os.path.join(ARTIFACTS, 'rfe-originals')

def read_frontmatter(path):
    """Read YAML frontmatter from a markdown file."""
    with open(path) as f:
        content = f.read()
    if not content.startswith('---'):
        return {}, content
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content
    fm = yaml.safe_load(parts[1])
    body = parts[2].strip()
    return fm or {}, body

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

def badge(passed):
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
    rfes = []
    review_files = sorted([f for f in os.listdir(REVIEWS_DIR) if f.endswith('-review.md')])

    for rf in review_files:
        rfe_id = rf.replace('-review.md', '')
        review_fm, review_body = read_frontmatter(os.path.join(REVIEWS_DIR, rf))

        task_path = os.path.join(TASKS_DIR, f'{rfe_id}.md')
        task_fm = {}
        if os.path.exists(task_path):
            task_fm, _ = read_frontmatter(task_path)
        title = task_fm.get('title', rfe_id)

        after_scores = review_fm.get('scores', {})
        revision_history = get_revision_history(review_body)
        before_scores = parse_before_scores(revision_history, after_scores)

        before_total = sum(before_scores.values())
        after_total = review_fm.get('score', sum(after_scores.values()))

        before_pass = before_total >= 8 and all(v > 0 for v in before_scores.values())
        after_pass = review_fm.get('pass', False)

        diff_text = generate_diff(rfe_id)
        removed_context = read_removed_context(rfe_id)

        rfes.append({
            'rfe_id': rfe_id,
            'title': title,
            'before_scores': before_scores,
            'after_scores': after_scores,
            'before_total': before_total,
            'after_total': after_total,
            'before_pass': before_pass,
            'after_pass': after_pass,
            'feasibility': review_fm.get('feasibility', ''),
            'revised': review_fm.get('revised', False),
            'needs_attention': review_fm.get('needs_attention', False),
            'recommendation': review_fm.get('recommendation', ''),
            'diff_text': diff_text,
            'removed_context': removed_context,
            'revision_history': revision_history,
        })

    n = len(rfes)
    before_passing = sum(1 for r in rfes if r['before_pass'])
    after_passing = sum(1 for r in rfes if r['after_pass'])
    avg_before = sum(r['before_total'] for r in rfes) / n if n else 0
    avg_after = sum(r['after_total'] for r in rfes) / n if n else 0

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
        padding: 0;
    }
    .page {
        page-break-after: always;
    }
    .page:last-child {
        page-break-after: avoid;
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
    .before-box {
        background: #fef3f3;
        border: 1px solid #e8c4c4;
    }
    .after-box {
        background: #f0f9f0;
        border: 1px solid #b8d4b8;
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
'''

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
        <p class="subtitle">{n} RFEs assessed, auto-revised, and re-assessed</p>

        <div class="summary-stats">
            <div class="stat-box">
                <div class="stat-value">{before_passing}/{n}</div>
                <div class="stat-label">Passing Before</div>
            </div>
            <div class="stat-arrow">&rarr;</div>
            <div class="stat-box">
                <div class="stat-value">{after_passing}/{n}</div>
                <div class="stat-label">Passing After</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{avg_before:.1f}</div>
                <div class="stat-label">Avg Score Before</div>
            </div>
            <div class="stat-arrow">&rarr;</div>
            <div class="stat-box">
                <div class="stat-value">{avg_after:.1f}</div>
                <div class="stat-label">Avg Score After</div>
            </div>
        </div>

        <table class="summary-table">
            <thead>
                <tr>
                    <th>RFE</th>
                    <th>Before</th>
                    <th></th>
                    <th>After</th>
                    <th></th>
                    <th>&Delta;</th>
                    <th>Removed</th>
                </tr>
            </thead>
            <tbody>
'''

    for r in rfes:
        d = r['after_total'] - r['before_total']
        rc = r['removed_context']
        if rc:
            blocks = rc.get('blocks', [])
            rc_text = f'{len(blocks)} block{"s" if len(blocks)!=1 else ""}'
        else:
            rc_text = '&mdash;'

        html += f'''        <tr>
            <td class="key-col">{html_escape(r['rfe_id'])}</td>
            <td>{r['before_total']}/10</td>
            <td>{badge(r['before_pass'])}</td>
            <td>{r['after_total']}/10</td>
            <td>{badge(r['after_pass'])}</td>
            <td class="{delta_class(d)}">{delta_text(d)}</td>
            <td>{rc_text}</td>
        </tr>
'''

    # Build revision summary bullets dynamically
    summary_bullets = []

    # Summarize per-criterion changes
    criterion_labels = {'what': 'WHAT', 'why': 'WHY', 'open_to_how': 'HOW', 'not_a_task': 'Not-a-task', 'right_sized': 'Right-sized'}
    for key, label in criterion_labels.items():
        improved = [r for r in rfes if r['before_scores'].get(key, 0) < r['after_scores'].get(key, 0)]
        degraded = [r for r in rfes if r['before_scores'].get(key, 0) > r['after_scores'].get(key, 0)]
        if improved:
            ids = ', '.join(r['rfe_id'] for r in improved)
            if len(improved) == n:
                summary_bullets.append(f'<li><strong>{label}:</strong> All {n} RFEs improved ({improved[0]["before_scores"].get(key, 0)}&rarr;{improved[0]["after_scores"].get(key, 0)}).</li>')
            else:
                summary_bullets.append(f'<li><strong>{label} improved:</strong> {len(improved)} RFE{"s" if len(improved)!=1 else ""} ({ids}).</li>')
        if degraded:
            ids = ', '.join(r['rfe_id'] for r in degraded)
            summary_bullets.append(f'<li><strong>{label} degraded:</strong> {len(degraded)} RFE{"s" if len(degraded)!=1 else ""} ({ids}).</li>')

    # Remaining gaps — criteria still below max
    for key, label in criterion_labels.items():
        below_max = [r for r in rfes if r['after_scores'].get(key, 0) < 2]
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

    for r in rfes:
        d = r['after_total'] - r['before_total']

        html += f'''
        <div class="page">
            <h1>{html_escape(r['rfe_id'])}</h1>
            <h2>{html_escape(r['title'])}</h2>

            <div class="score-section">
                <div class="score-summary">
                    <div class="score-box before-box">
                        <div class="score-label">Before</div>
                        <div class="score-value">{r['before_total']}/10</div>
                        <div class="score-result">{badge(r['before_pass'])}</div>
                    </div>
                    <div class="score-arrow">&rarr;</div>
                    <div class="score-box after-box">
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
            html += f'''                        <tr>
                            <td class="criterion">{crit_name}</td>
                            <td>{bv}/2</td>
                            <td>{av}/2</td>
                            <td class="{score_change_class(bv, av)}">{score_change_text(bv, av)}</td>
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
</body>
</html>
'''

    output_path = os.path.join(ARTIFACTS, 'review-report.html')
    with open(output_path, 'w') as f:
        f.write(html)
    print(f'Report written to {output_path}')
    print(f'{n} RFEs, {before_passing}/{n} passing before, {after_passing}/{n} passing after')
    print(f'{removed_count} RFEs with removed context ({total_blocks} blocks)')

if __name__ == '__main__':
    main()
