"""بررسی استاتیک: هر کلید t("...") باید در locales/strings.py باشد و placeholderها با kwargها بخوانند."""
import ast, glob, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def collect():
    used = {}   # key -> list of kw sets
    dyn = {}    # prefix -> kw sets
    for f in glob.glob(f"{ROOT}/handlers/*.py") + glob.glob(f"{ROOT}/core/*.py") + [f"{ROOT}/main.py"]:
        tree = ast.parse(open(f).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t" and node.args:
                a = node.args[0]
                kws = {k.arg for k in node.keywords if k.arg}
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    used.setdefault(a.value, []).append((kws, os.path.basename(f)))
                elif isinstance(a, ast.BinOp) and isinstance(a.left, ast.Constant):
                    dyn.setdefault(a.left.value, []).append((kws, os.path.basename(f)))
                elif isinstance(a, ast.IfExp):   # t("a" if x else "b")
                    for br in (a.body, a.orelse):
                        if isinstance(br, ast.Constant):
                            used.setdefault(br.value, []).append((kws, os.path.basename(f)))
    return used, dyn

if __name__ == "__main__":
    used, dyn = collect()
    if "--list" in sys.argv:
        for k in sorted(used):
            print(k, sorted(set().union(*[s for s, _ in used[k]])))
        print("DYNAMIC", {k: sorted(set().union(*[s for s, _ in v])) for k, v in dyn.items()})
        sys.exit()
    from locales.strings import S
    problems = 0
    for k, calls in used.items():
        if k not in S:
            print("MISSING key:", k, calls[0][1]); problems += 1; continue
        for idx, lang in enumerate(("fa", "en")):
            ph = set(re.findall(r"\{(\w+)\}", S[k][idx]))
            for kws, fname in calls:
                if ph - kws:
                    print(f"PLACEHOLDER without value: {k}[{lang}] needs {sorted(ph - kws)} ({fname})"); problems += 1
    for prefix, calls in dyn.items():
        keys = [k for k in S if k.startswith(prefix)]
        if not keys:
            print("MISSING dynamic prefix:", prefix); problems += 1
    for k, (fa, en) in S.items():
        if set(re.findall(r"\{(\w+)\}", fa)) != set(re.findall(r"\{(\w+)\}", en)):
            print("fa/en placeholder mismatch:", k); problems += 1
    unused = [k for k in S if k not in used and not any(k.startswith(p) for p in dyn)]
    print("unused keys:", unused)
    print("PROBLEMS:", problems)
    sys.exit(1 if problems else 0)
