LISTS = ("bulletList", "orderedList")
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
        value = node.get("text") or ""
        link = next((mark for mark in node.get("marks") or [] if isinstance(mark, dict) and mark.get("type") == "link"),
                    None)
        return f"[{value}]({_attr(link, 'href')})" if link and _attr(link, "href") else value
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return "@" + str(_attr(node, "text") or "").lstrip("@")
    if kind == "emoji":
        return str(_attr(node, "text") or _attr(node, "shortName") or "")
    if kind == "inlineCard":
        return str(_attr(node, "url") or "")
    children = _children(node)
    return _inline(children) if children else str(_attr(node, "text") or "")


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
        return "#" * int(_attr(node, "level") or 1) + " " + _inline(children)
    if kind in LISTS:
        return _list(node, 0)
    if kind == "codeBlock":
        return f"```{_attr(node, 'language') or ''}\n{_inline(children)}\n```"
    if kind == "blockquote":
        return "\n".join("> " + line if line else ">" for line in _blocks(children).splitlines())
    if kind == "rule":
        return "---"
    if kind == "table":
        return "\n".join(" | ".join(_blocks(_children(cell)).replace("\n", " ") for cell in _children(row))
                         for row in children)
    if children and all(child.get("type") in INLINE for child in children):
        return _inline(children)
    return _blocks(children) if children else str(_attr(node, "text") or "")


def _list(node, depth):
    lines = []
    for number, item in enumerate(_children(node), 1):
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
