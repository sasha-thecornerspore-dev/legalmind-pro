"""Guards against GUI-construction bugs that only surface at runtime."""
import ast
import os

GUI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "gui.py")

_WIDGETS = {"Frame", "Label", "Button", "Canvas", "Text", "Entry", "Toplevel",
            "LabelFrame", "Scrollbar", "Checkbutton", "Radiobutton", "Scale",
            "Listbox", "Message", "Spinbox", "PanedWindow", "Menu"}


def test_no_tuple_padx_pady_in_widget_constructors():
    """A widget's own padx/pady must be a single distance; a tuple stringifies
    to e.g. 'bad screen distance "0 8"' and crashes at build time. Tuples are
    only valid on the geometry calls (.pack/.grid/.place)."""
    tree = ast.parse(open(GUI, encoding="utf-8").read())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr in _WIDGETS
                and isinstance(f.value, ast.Name) and f.value.id in ("tk", "ttk")):
            for kw in node.keywords:
                if kw.arg in ("padx", "pady") and isinstance(
                        kw.value, (ast.Tuple, ast.List)):
                    bad.append((node.lineno, kw.arg))
    assert not bad, f"tuple padx/pady in widget constructors at: {bad}"
