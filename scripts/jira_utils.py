"""Shared Jira API, ADF conversion, and content processing utilities.

Used by both submit.py (standard) and split_submit.py (split submissions).

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


# ─── HTTP Layer ───────────────────────────────────────────────────────────────

def make_request(url, user, token, body=None, method=None):
    """HTTP request with Basic Auth. Returns parsed JSON or None for 204."""
    credentials = base64.b64encode(f"{user}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        if resp.status == 204:
            return None
        resp_body = resp.read()
        if not resp_body:
            return None
        return json.loads(resp_body)


def api_call(server, path, user, token, body=None, method=None):
    """Build full URL and call make_request."""
    url = f"{server.rstrip('/')}/rest/api/3{path}"
    return make_request(url, user, token, body, method)


def api_call_with_retry(server, path, user, token, body=None, method=None,
                        max_retries=3):
    """Wrap api_call with retry on transient errors."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return api_call(server, path, user, token, body, method)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", 1))
                wait = max(retry_after, 1)
                print(f"  Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                last_error = e
                continue
            if e.code in (502, 503, 504):
                wait = 4 ** attempt  # 1, 4, 16
                print(f"  HTTP {e.code}, retrying in {wait}s...",
                      file=sys.stderr)
                time.sleep(wait)
                last_error = e
                continue
            error_body = e.read().decode("utf-8", errors="replace")
            print(f"HTTP {e.code}: {error_body}", file=sys.stderr)
            raise
        except urllib.error.URLError as e:
            wait = 4 ** attempt
            print(f"  Network error: {e.reason}, retrying in {wait}s...",
                  file=sys.stderr)
            time.sleep(wait)
            last_error = e
    raise last_error


def require_env():
    """Read and validate Jira env vars. Returns (server, user, token)."""
    server = os.environ.get("JIRA_SERVER")
    user = os.environ.get("JIRA_USER")
    token = os.environ.get("JIRA_TOKEN")
    return server, user, token


# ─── Jira Operations ─────────────────────────────────────────────────────────

def get_issue(server, user, token, key, fields=None):
    """GET /rest/api/3/issue/{key}"""
    path = f"/issue/{key}"
    if fields:
        path += f"?fields={','.join(fields)}"
    return api_call_with_retry(server, path, user, token)


def get_comments(server, user, token, issue_key):
    """GET all comments for an issue, handling pagination."""
    comments = []
    start_at = 0
    while True:
        path = f"/issue/{issue_key}/comment?startAt={start_at}&maxResults=100"
        data = api_call_with_retry(server, path, user, token)
        batch = data.get("comments", [])
        comments.extend(batch)
        if start_at + len(batch) >= data.get("total", 0):
            break
        start_at += len(batch)
    return comments


def add_comment(server, user, token, issue_key, body_adf):
    """POST a comment with ADF body."""
    path = f"/issue/{issue_key}/comment"
    return api_call_with_retry(server, path, user, token,
                               body={"body": body_adf})


def create_issue(server, user, token, project, issue_type, summary,
                 description_adf, priority, labels=None):
    """POST /rest/api/3/issue — returns the created issue key."""
    body = {
        "fields": {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "description": description_adf,
            "priority": {"name": priority},
        }
    }
    if labels:
        body["fields"]["labels"] = labels
    result = api_call_with_retry(server, "/issue", user, token, body=body)
    return result["key"]


def update_issue(server, user, token, issue_key, summary, description_adf):
    """PUT to update an existing issue's summary and description."""
    body = {
        "fields": {
            "summary": summary,
            "description": description_adf,
        }
    }
    path = f"/issue/{issue_key}"
    api_call_with_retry(server, path, user, token, body=body, method="PUT")


def add_labels(server, user, token, issue_key, labels):
    """Add labels to an existing issue without removing existing ones."""
    body = {
        "update": {
            "labels": [{"add": label} for label in labels]
        }
    }
    path = f"/issue/{issue_key}"
    api_call_with_retry(server, path, user, token, body=body, method="PUT")


def create_issue_link(server, user, token, type_name, inward_key, outward_key):
    """POST /rest/api/3/issueLink"""
    body = {
        "type": {"name": type_name},
        "inwardIssue": {"key": inward_key},
        "outwardIssue": {"key": outward_key},
    }
    api_call_with_retry(server, "/issueLink", user, token, body=body)


def get_transitions(server, user, token, issue_key):
    """GET available transitions for an issue."""
    path = f"/issue/{issue_key}/transitions"
    data = api_call_with_retry(server, path, user, token)
    return data.get("transitions", [])


def do_transition(server, user, token, issue_key, transition_id, fields=None):
    """POST a transition, optionally setting fields (e.g. resolution)."""
    body = {"transition": {"id": transition_id}}
    if fields:
        body["fields"] = fields
    path = f"/issue/{issue_key}/transitions"
    api_call_with_retry(server, path, user, token, body=body)


# ─── ADF Helpers ──────────────────────────────────────────────────────────────

def _adf_doc(content):
    """Wrap content nodes in an ADF document."""
    return {"type": "doc", "version": 1, "content": content}


def _adf_paragraph(text_nodes):
    """Create an ADF paragraph from text nodes."""
    return {"type": "paragraph", "content": text_nodes}


def _adf_text(text, marks=None):
    """Create an ADF text node, optionally with marks."""
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def _adf_heading(level, text_nodes):
    """Create an ADF heading node."""
    return {"type": "heading", "attrs": {"level": level},
            "content": text_nodes}


def _adf_code_block(text, language=""):
    """Create an ADF codeBlock node."""
    node = {"type": "codeBlock", "content": [_adf_text(text)]}
    if language:
        node["attrs"] = {"language": language}
    return node


def _adf_bullet_list(items):
    """Create an ADF bulletList from a list of content node lists."""
    return {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [_adf_paragraph(nodes)]}
            for nodes in items
        ],
    }


def _adf_ordered_list(items):
    """Create an ADF orderedList from a list of content node lists."""
    return {
        "type": "orderedList",
        "content": [
            {"type": "listItem", "content": [_adf_paragraph(nodes)]}
            for nodes in items
        ],
    }


def _adf_rule():
    """Create an ADF horizontal rule."""
    return {"type": "rule"}


def _adf_table(rows, has_header=True):
    """Create an ADF table from rows of cell text lists.

    Each row is a list of cell strings. If has_header, the first row
    uses tableHeader cells; remaining rows use tableCell.
    """
    adf_rows = []
    for row_idx, cells in enumerate(rows):
        is_header = has_header and row_idx == 0
        cell_type = "tableHeader" if is_header else "tableCell"
        adf_cells = []
        for cell_text in cells:
            adf_cells.append({
                "type": cell_type,
                "content": [_adf_paragraph(_parse_inline(cell_text.strip()))],
            })
        adf_rows.append({"type": "tableRow", "content": adf_cells})
    return {"type": "table", "content": adf_rows}


def _parse_inline(text):
    """Parse inline markdown formatting into ADF text nodes with marks.

    Handles: **bold**, *italic*, ~~strike~~, `code`, [text](url)
    """
    nodes = []
    pattern = re.compile(
        r'(\*\*(?P<bold>.+?)\*\*)'
        r'|(\*(?P<italic>.+?)\*)'
        r'|(~~(?P<strike>.+?)~~)'
        r'|(`(?P<code>[^`]+)`)'
        r'|(\[(?P<link_text>[^\]]*)\]\((?P<link_url>[^)]+)\))'
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            nodes.append(_adf_text(text[pos:m.start()]))

        if m.group("bold") is not None:
            nodes.append(_adf_text(m.group("bold"), [{"type": "strong"}]))
        elif m.group("italic") is not None:
            nodes.append(_adf_text(m.group("italic"), [{"type": "em"}]))
        elif m.group("strike") is not None:
            nodes.append(_adf_text(m.group("strike"), [{"type": "strike"}]))
        elif m.group("code") is not None:
            nodes.append(_adf_text(m.group("code"), [{"type": "code"}]))
        elif m.group("link_text") is not None:
            nodes.append(_adf_text(
                m.group("link_text"),
                [{"type": "link",
                  "attrs": {"href": m.group("link_url")}}]
            ))
        pos = m.end()

    if pos < len(text):
        nodes.append(_adf_text(text[pos:]))

    return nodes if nodes else [_adf_text(text)]


def markdown_to_adf(markdown):
    """Convert markdown to Atlassian Document Format.

    Handles: headings, paragraphs, bullet/ordered lists, bold, italic,
    strikethrough, code spans, code blocks, blockquotes, tables,
    horizontal rules, links, and checkboxes (as text).
    """
    lines = markdown.split("\n")
    content = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Code block
        if line.startswith("```"):
            lang = line[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            content.append(_adf_code_block("\n".join(code_lines), lang))
            continue

        # Heading
        heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            content.append(_adf_heading(level, _parse_inline(text)))
            i += 1
            continue

        # Horizontal rule
        if re.match(r'^---+\s*$', line):
            content.append(_adf_rule())
            i += 1
            continue

        # Bullet list
        if re.match(r'^[-*]\s', line) or re.match(r'^- \[[ x]\]\s', line):
            items = []
            while i < len(lines) and (re.match(r'^[-*]\s', lines[i]) or
                                       re.match(r'^- \[[ x]\]\s', lines[i])):
                item_text = re.sub(r'^[-*]\s+', '', lines[i])
                items.append(_parse_inline(item_text))
                i += 1
            content.append(_adf_bullet_list(items))
            continue

        # Ordered list
        if re.match(r'^\d+\.\s', line):
            items = []
            while i < len(lines) and re.match(r'^\d+\.\s', lines[i]):
                item_text = re.sub(r'^\d+\.\s+', '', lines[i])
                items.append(_parse_inline(item_text))
                i += 1
            content.append(_adf_ordered_list(items))
            continue

        # Blockquote
        if line.startswith("> ") or line == ">":
            quote_lines = []
            while i < len(lines) and (lines[i].startswith("> ") or
                                       lines[i] == ">"):
                quote_lines.append(re.sub(r'^>\s?', '', lines[i]))
                i += 1
            quote_md = "\n".join(quote_lines)
            inner = markdown_to_adf(quote_md)
            content.append({
                "type": "blockquote",
                "content": inner.get("content", []),
            })
            continue

        # Table
        if re.match(r'^\|.+\|', line):
            table_rows = []
            while i < len(lines) and re.match(r'^\|.+\|', lines[i]):
                row_text = lines[i].strip()
                # Skip separator rows (| --- | --- |)
                if re.match(r'^\|[\s\-:|]+\|$', row_text):
                    i += 1
                    continue
                # Split cells, dropping empty first/last from leading/trailing |
                cells = row_text.split("|")
                cells = [c for c in cells[1:-1]]  # drop empty first/last
                table_rows.append(cells)
                i += 1
            if table_rows:
                content.append(_adf_table(table_rows, has_header=True))
            continue

        # Empty line — skip
        if not line.strip():
            i += 1
            continue

        # Paragraph — accumulate consecutive non-empty, non-special lines
        para_lines = []
        while i < len(lines) and lines[i].strip() and \
                not lines[i].startswith("#") and \
                not lines[i].startswith("```") and \
                not re.match(r'^[-*]\s', lines[i]) and \
                not re.match(r'^\d+\.\s', lines[i]) and \
                not re.match(r'^---+\s*$', lines[i]) and \
                not re.match(r'^\|.+\|', lines[i]):
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            text = " ".join(para_lines)
            content.append(_adf_paragraph(_parse_inline(text)))

    return _adf_doc(content) if content else \
        _adf_doc([_adf_paragraph([_adf_text("")])])


def text_to_adf_codeblock(text):
    """Wrap raw text in a single ADF codeBlock — for archival comments."""
    return _adf_doc([_adf_code_block(text)])


def text_to_adf_paragraph(text):
    """Wrap text in a simple ADF paragraph — for short status comments."""
    return _adf_doc([_adf_paragraph([_adf_text(text)])])


def archival_comment_adf(header, markdown_body):
    """Build ADF for an archival comment: header paragraph + codeBlock body."""
    return _adf_doc([
        _adf_paragraph(_parse_inline(header)),
        _adf_code_block(markdown_body),
    ])


# ─── ADF → Markdown ──────────────────────────────────────────────────────────

def adf_to_markdown(node, list_depth=0):
    """Convert Atlassian Document Format (ADF) JSON to markdown."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node

    if isinstance(node, list):
        return "".join(adf_to_markdown(item, list_depth) for item in node)

    if not isinstance(node, dict):
        return ""

    node_type = node.get("type", "")
    content = node.get("content", [])
    attrs = node.get("attrs", {})

    if node_type == "doc":
        return adf_to_markdown(content, list_depth)

    if node_type == "text":
        text = node.get("text", "")
        for mark in node.get("marks", []):
            mark_type = mark.get("type", "")
            if mark_type == "strong":
                text = f"**{text}**"
            elif mark_type == "em":
                text = f"*{text}*"
            elif mark_type == "code":
                text = f"`{text}`"
            elif mark_type == "strike":
                text = f"~~{text}~~"
            elif mark_type == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text

    if node_type == "paragraph":
        inner = adf_to_markdown(content, list_depth)
        return f"{inner}\n\n"

    if node_type == "heading":
        level = attrs.get("level", 1)
        inner = adf_to_markdown(content, list_depth)
        return f"{'#' * level} {inner}\n\n"

    if node_type == "bulletList":
        items = adf_to_markdown(content, list_depth)
        return f"{items}\n" if list_depth == 0 else items

    if node_type == "orderedList":
        result = []
        for idx, item in enumerate(content, 1):
            item_text = adf_to_markdown(
                item.get("content", []), list_depth + 1
            ).strip()
            indent = "  " * list_depth
            result.append(f"{indent}{idx}. {item_text}\n")
        return "".join(result) + ("\n" if list_depth == 0 else "")

    if node_type == "listItem":
        item_parts = []
        for child in content:
            child_type = child.get("type", "")
            if child_type in ("bulletList", "orderedList"):
                item_parts.append(adf_to_markdown(child, list_depth + 1))
            else:
                item_parts.append(
                    adf_to_markdown(child, list_depth).strip()
                )
        indent = "  " * list_depth
        first = item_parts[0] if item_parts else ""
        rest = "".join(item_parts[1:])
        return f"{indent}- {first}\n{rest}"

    if node_type == "codeBlock":
        lang = attrs.get("language", "")
        inner = adf_to_markdown(content, list_depth)
        return f"```{lang}\n{inner}\n```\n\n"

    if node_type == "blockquote":
        inner = adf_to_markdown(content, list_depth)
        lines = inner.strip().split("\n")
        quoted = "\n".join(f"> {line}" for line in lines)
        return f"{quoted}\n\n"

    if node_type == "rule":
        return "---\n\n"

    if node_type == "table":
        rows = []
        for row_node in content:
            if row_node.get("type") == "tableRow":
                cells = []
                for cell in row_node.get("content", []):
                    cell_text = adf_to_markdown(
                        cell.get("content", []), list_depth
                    ).strip()
                    cell_text = cell_text.replace("\n", " ")
                    cells.append(cell_text)
                rows.append(cells)
        if not rows:
            return ""
        col_count = max(len(r) for r in rows)
        lines = []
        for i, row in enumerate(rows):
            row += [""] * (col_count - len(row))
            lines.append("| " + " | ".join(row) + " |")
            if i == 0:
                lines.append("| " + " | ".join(["---"] * col_count) + " |")
        return "\n".join(lines) + "\n\n"

    if node_type in ("mediaSingle", "media"):
        return ""

    if node_type == "hardBreak":
        return "\n"

    if node_type == "inlineCard":
        url = attrs.get("url", "")
        return f"[{url}]({url})" if url else ""

    if node_type == "emoji":
        return attrs.get("text", attrs.get("shortName", ""))

    if node_type == "panel":
        inner = adf_to_markdown(content, list_depth)
        return f"> {inner.strip()}\n\n"

    if node_type == "expand":
        title = attrs.get("title", "")
        inner = adf_to_markdown(content, list_depth)
        header = f"**{title}**\n\n" if title else ""
        return f"{header}{inner}"

    # Fallback: recurse into content
    return adf_to_markdown(content, list_depth)


# ─── Content Processing ──────────────────────────────────────────────────────

def strip_metadata(markdown):
    """Remove artifact metadata and revision notes from RFE markdown.

    Strips:
    - YAML frontmatter (--- delimited block at start of file)
    - # RFE-NNN: / # STRAT-NNN: Title headings (duplicates Summary field)
    - Legacy inline metadata lines (now in frontmatter):
      **Jira Key**, **Size**, **Split from**, **Priority**, **Source RFE**
    - ### Revision Notes sections (to next ## or EOF)
    - > *Review note: ...* blockquotes
    - <!-- REVISION NOTE: ... --> HTML comments
    """
    # Strip YAML frontmatter if present
    frontmatter_match = re.match(r'^---\s*\n.*?\n---\s*\n', markdown,
                                 re.DOTALL)
    if frontmatter_match:
        markdown = markdown[frontmatter_match.end():]

    lines = markdown.split("\n")
    result = []
    in_revision_notes = False

    for line in lines:
        # Skip title heading — duplicates Summary
        if re.match(r'^#\s+(RFE-\d+|STRAT-\d+):', line):
            continue

        # Skip metadata lines (legacy inline format, now in frontmatter)
        if re.match(r'^\*\*(Jira Key|Size|Split from|Priority|'
                    r'Source RFE)\*\*:', line):
            continue

        # Skip HTML revision comments
        if re.match(r'^\s*<!--\s*REVISION NOTE:', line):
            continue

        # Skip review note blockquotes
        if re.match(r'^>\s*\*Review note:', line):
            continue

        # Track revision notes section
        if re.match(r'^###\s+Revision Notes', line):
            in_revision_notes = True
            continue
        if in_revision_notes:
            if re.match(r'^##\s', line):
                in_revision_notes = False
            else:
                continue

        result.append(line)

    # Clean up multiple consecutive blank lines
    cleaned = "\n".join(result)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


