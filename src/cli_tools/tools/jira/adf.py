import datetime

LISTS = ("bulletList", "orderedList")
MARKS = (("code", "`"), ("em", "*"), ("strong", "**"), ("strike", "~~"))
INLINE = ("text", "hardBreak", "mention", "emoji", "inlineCard", "date", "status", "placeholder")


def to_text(node):
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    return _block(node).rstrip()


def _children(node):
    content = node.get("content") if isinstance(node, dict) else None
    return [child for child in content if isinstance(child, dict)] if isinstance(content, list) else []


def _attr(node, name):
    attrs = node.get("attrs")
    return attrs.get(name) if isinstance(attrs, dict) else None


def _inline(nodes):
    return "".join(_inline_node(node) for node in nodes)


def _inline_node(node):
    kind = node.get("type")
    if kind == "text":
        return _marked(node.get("text") or "", node.get("marks"))
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        who = str(_attr(node, "text") or _attr(node, "id") or "").lstrip("@")
        return "@" + who if who else ""
    if kind == "date":
        return _date(_attr(node, "timestamp"))
    if kind == "emoji":
        return str(_attr(node, "text") or _attr(node, "shortName") or "")
    if kind == "inlineCard":
        return str(_attr(node, "url") or "")
    children = _children(node)
    return _inline(children) if children else str(_attr(node, "text") or "")


def _marked(value, marks):
    marks = [mark for mark in marks if isinstance(mark, dict)] if isinstance(marks, list) else []
    core = value.strip()
    if not marks or not core:
        return value
    kinds = {mark.get("type") for mark in marks}
    for kind, wrap in MARKS:
        if kind in kinds:
            core = f"{wrap}{core}{wrap}"
    href = next((_attr(mark, "href") for mark in marks if mark.get("type") == "link"), None)
    if href:
        core = f"[{core}]({href})"
    lead = len(value) - len(value.lstrip())
    return value[:lead] + core + value[len(value.rstrip()):]


def _table(rows):
    cells = [[_blocks(_children(cell)).replace("\n", " ").replace("|", "\\|") for cell in _children(row)]
             for row in rows]
    width = max((len(row) for row in cells), default=0)
    if not width:
        return ""
    lines = ["| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in cells]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def _level(value):
    """A heading level, 1 to 6, whatever the node says."""
    try:
        return min(max(int(value), 1), 6)
    except (TypeError, ValueError):
        return 1


def _date(timestamp):
    """A date node's timestamp (milliseconds, as text) as YYYY-MM-DD, in UTC as Jira shows it."""
    try:
        return datetime.datetime.fromtimestamp(int(timestamp) / 1000, datetime.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return str(timestamp or "")


def _start(node):
    order = _attr(node, "order")
    if isinstance(order, str) and order.isdigit():
        order = int(order)
    return order if isinstance(order, int) and not isinstance(order, bool) and order >= 0 else 1


def _blocks(nodes):
    return "\n\n".join(text for text in (_block(node) for node in nodes) if text)


def _block(node):
    kind = node.get("type")
    children = _children(node)
    if kind in INLINE:
        return _inline_node(node)
    if kind == "paragraph":
        return _inline(children)
    if kind == "heading":
        return "#" * _level(_attr(node, "level")) + " " + _inline(children)
    if kind in ("expand", "nestedExpand"):
        title = _attr(node, "title")
        return "\n\n".join(text for text in (f"**{title}**" if title else "", _blocks(children)) if text)
    if kind in LISTS:
        return _list(node, 0)
    if kind == "codeBlock":
        return f"```{_attr(node, 'language') or ''}\n{_inline(children)}\n```"
    if kind == "blockquote":
        return "\n".join("> " + line if line else ">" for line in _blocks(children).splitlines())
    if kind == "rule":
        return "---"
    if kind in ("blockCard", "embedCard"):
        return str(_attr(node, "url") or "")
    if kind == "media":
        alt = _attr(node, "alt")
        return f"[image: {alt}]" if alt else "[attachment]"
    if kind == "table":
        return _table(children)
    if children and all(child.get("type") in INLINE for child in children):
        return _inline(children)
    return _blocks(children) if children else str(_attr(node, "text") or "")


def _list(node, depth):
    lines = []
    for number, item in enumerate(_children(node), _start(node)):
        marker = f"{number}. " if node.get("type") == "orderedList" else "- "
        first = True
        for child in _children(item):
            if child.get("type") in LISTS:
                lines.append(_list(child, depth + 1))
                continue
            text = _block(child)
            prefix = "  " * depth + (marker if first else " " * len(marker))
            lines.append("\n".join(prefix + line if i == 0 else " " * len(prefix) + line
                                   for i, line in enumerate(text.splitlines() or [""])))
            first = False
        if first:
            lines.append("  " * depth + marker.rstrip())
    return "\n".join(lines)
