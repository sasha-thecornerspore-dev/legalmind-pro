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


def test_every_self_method_reference_exists():
    """`command=self._x` or `self._x()` naming a method that does not exist
    only fails when that button is clicked -- catch it here instead."""
    tree = ast.parse(open(GUI, encoding="utf-8").read())
    app = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "LegalMindApp")
    defined = {n.name for n in app.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assigned = set()
    for node in ast.walk(app):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                    and t.value.id == "self"):
                assigned.add(t.attr)
    missing = set()
    for node in ast.walk(app):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and node.attr.startswith("_")
                and isinstance(node.ctx, ast.Load)
                and node.attr not in defined and node.attr not in assigned):
            missing.add(node.attr)
    assert not missing, f"self.<name> used but never defined: {sorted(missing)}"
