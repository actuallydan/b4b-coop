"""ASCII FBX (7.x) -> binary FBX, for Blender's importer (it reads only binary: "ASCII FBX files are not supported").
Cinema 4D, older Max/Maya exports and many downloads are ASCII. Parsed here, written with Blender's own encoder
(io_scene_fbx.encode_bin); value types follow what the importer asserts: object ids int64, "Class::Name" names as the
binary "Name\\x00\\x01Class", Properties70 numbers by their property type (int/enum/bool int32, KTime/ULongLong int64,
the rest float64), arrays by name.

    to_binary(ascii_path, out_path) -> FBX version
"""
import re

INT_ARRAYS = {"PolygonVertexIndex", "Edges", "UVIndex", "NormalsIndex", "BinormalsIndex", "TangentsIndex", "Materials",
              "Indexes", "ColorIndex", "Smoothing", "KeyAttrFlags", "KeyAttrRefCount", "VisibilityIndex"}
INT64_ARRAYS = {"KeyTime"}
FLOAT32_ARRAYS = {"KeyValueFloat", "KeyAttrDataFloat"}
FLOAT_ARRAYS = {"Vertices", "Normals", "Binormals", "Tangents", "UV", "Weights", "Transform", "TransformLink",
                "TransformAssociateModel", "Matrix", "Colors", "NormalsW", "BinormalsW", "TangentsW", "FullWeights",
                "EdgeCrease", "VertexCrease"}
P_INT = {"int", "Integer", "enum", "Enum", "bool", "Bool", "Visibility Inheritance", "Short", "UShort", "Char",
         "UChar", "Byte"}
P_INT64 = {"KTime", "ULongLong", "LongLong"}

_TOKEN = re.compile(r'''[ \t\r]*(?:(;[^\n]*)|("(?:[^"\\]|\\.)*")|(\*\d+)|([{}:,])|([^\s{}:,"]+)|(\n))''')


def _tokens(text):
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "": return
            raise ValueError(f"ASCII FBX: can't read at offset {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        comment, string, count, punct, word, nl = m.groups()
        if comment is not None: continue
        if string is not None: yield ("S", string[1:-1].replace("&quot;", '"'))
        elif count is not None: yield ("*", int(count[1:]))
        elif punct is not None: yield (punct, punct)
        elif word is not None: yield ("W", word)
        elif nl is not None: yield ("NL", None)


class _Node:
    __slots__ = ("key", "props", "children", "array")

    def __init__(self, key):
        self.key, self.props, self.children, self.array = key, [], [], None


def parse(text):
    """-> list of top-level _Node (props: ('S', str) / ('W', word); array: list of number words)."""
    toks = list(_tokens(text))
    i, n = 0, len(toks)
    root = _Node("")
    stack = [root]
    while i < n:
        t, v = toks[i]
        if t == "NL" or t == ",": i += 1; continue
        if t == "}":
            stack.pop(); i += 1; continue
        if t == "W" and i + 1 < n and toks[i + 1][0] == ":":
            node = _Node(v)
            stack[-1].children.append(node)
            i += 2
            if node.key == "a" and stack[-1].array is not None:     # array data: numbers up to the closing brace
                while i < n and toks[i][0] != "}":
                    if toks[i][0] == "W": stack[-1].array.append(toks[i][1])
                    i += 1
                stack[-1].children.pop()
                continue
            # props: up to the end of the line, continued when the line ends with a comma
            while i < n:
                t2, v2 = toks[i]
                if t2 == "NL":
                    if node.props and toks[i - 1][0] == ",": i += 1; continue
                    break
                if t2 == "{":
                    stack.append(node); i += 1; break
                if t2 == "*":
                    node.array = []; i += 1; continue
                if t2 in ("S", "W"): node.props.append((t2, v2))
                i += 1
            continue
        raise ValueError(f"ASCII FBX: unexpected {v!r}")
    return root.children


def _is_int(w):
    return re.fullmatch(r"[-+]?\d+", w) is not None


def _build(node, parent_key, eb):
    e = eb.FBXElem(node.key.encode())
    if node.array is not None:
        k, vals = node.key, node.array
        if k in INT64_ARRAYS: e.add_int64_array([int(x) for x in vals])
        elif k in FLOAT32_ARRAYS: e.add_float32_array([float(x) for x in vals])
        elif k in INT_ARRAYS or (k not in FLOAT_ARRAYS and all(_is_int(x) for x in vals)):
            e.add_int32_array([int(x) for x in vals])
        else: e.add_float64_array([float(x) for x in vals])
    elif node.key == "P" and len(node.props) >= 4:
        ptype = node.props[1][1]
        for j, (t, v) in enumerate(node.props):
            if j < 4 or t == "S": e.add_string(v.encode())
            elif ptype in P_INT64: e.add_int64(int(float(v)))
            elif ptype in P_INT: e.add_int32(int(float(v)))
            else: e.add_float64(float(v))
    else:
        for j, (t, v) in enumerate(node.props):
            if t == "S":
                if node.key == "FileId":
                    e.add_bytes(v.encode("latin-1", "replace")); continue
                if node.key == "Content":                      # embedded media: base64 in ASCII, raw bytes in binary
                    import base64
                    b = v[:len(v) - (len(v) % 4 == 1)]
                    try:
                        e.add_bytes(base64.b64decode(b + "=" * (-len(b) % 4)))
                    except ValueError:
                        e.add_bytes(b"")                       # unreadable: the image is looked up by file name
                    continue
                m = re.fullmatch(r"(\w+)::(.*)", v, re.S)
                if m and parent_key == "Objects":
                    e.add_string(m.group(2).encode() + b"\x00\x01" + m.group(1).encode())
                else:
                    e.add_string(v.encode())
            elif _is_int(v):
                x = int(v)
                if (parent_key == "Objects" and j == 0) or (node.key == "C" and j in (1, 2)) or node.key == "Node" or \
                        not -2 ** 31 <= x < 2 ** 31:
                    e.add_int64(x)
                else:
                    e.add_int32(x)
            elif re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", v):
                e.add_float64(float(v))
            elif len(v) == 1:
                e.add_char(v.encode())                       # Shading: Y, T ...
            else:
                e.add_string(v.encode())
    if node.key == "Content" and not node.props:
        e.add_bytes(b"")
    for c in node.children:
        e.elems.append(_build(c, node.key, eb))
    return e


def to_binary(src, dst):
    from io_scene_fbx import encode_bin as eb
    with open(src, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    m = re.search(r"FBXVersion:\s*(\d+)", text)
    version = int(m.group(1)) if m else 7400
    if version < 7100:
        raise SystemExit(f"{src}: ASCII FBX version {version}: too old for Blender (7.1+); export it again as FBX 2014+")
    # embedded media: Content: , then one quoted base64 chunk per line ("...=",), each padded on its own: decoded
    # chunk by chunk and joined into one base64 string for the parser
    import base64

    def _content(m):
        data = b"".join(base64.b64decode(c + "=" * (-len(c) % 4)) for c in re.findall(r'"([^"]*)"', m.group(2)))
        return m.group(1) + '"' + base64.b64encode(data).decode() + '"\n'
    text = re.sub(r'(Content:\s*,)[ \t]*\n((?:[ \t]*"[^\n]*\n?)+)', _content, text)
    root = eb.FBXElem(b"")
    for n in parse(text):
        root.elems.append(_build(n, "", eb))
    if not any(x.id == b"FileId" for x in root.elems):
        fid = eb.FBXElem(b"FileId"); fid.add_bytes(b"\0" * 16); root.elems.append(fid)
    if not any(x.id == b"CreationTime" for x in root.elems):
        ct = eb.FBXElem(b"CreationTime"); ct.add_string(b"1970-01-01 10:00:00:000"); root.elems.append(ct)
    eb.write(dst, root, version)
    return version
