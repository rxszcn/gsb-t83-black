"""模块级带括号的 annotated assignment 过一遍 black，运行时记这条注解的方式会变。
跑法：PYTHONPATH=src .venv/bin/python repro/annassign.py
"""
import subprocess
import sys

import black
from black import Mode, format_str

SAMPLES = {
    "A": "((x)): int = 5\n",
    "B": "(x): int = 5\n",
    "C": "(((y))): int = 5\n",
    "D": "((obj.attr)): int = 5\n",
    "E": "((z)): int\n",
    "F": "((w)) = 5\n",
}


def facts(text, label):
    try:
        code = compile(text, "<t>", "exec")
    except Exception as exc:
        print("   ", label, "编译失败", type(exc).__name__, str(exc)[:60])
        return
    inner = [c.co_name for c in code.co_consts if hasattr(c, "co_name")]
    ns = {"__name__": "m", "obj": type("O", (), {})()}
    try:
        exec(code, ns)
    except Exception as exc:
        print("   ", label, "执行抛", type(exc).__name__, str(exc)[:40],
              "| 内嵌函数", inner)
        return
    print("   ", label, "内嵌函数", inner,
          "| __annotations__", ns.get("__annotations__"),
          "| 值", {k: v for k, v in ns.items() if not k.startswith("__")})


for key, src in SAMPLES.items():
    print(key, "输入", repr(src))
    try:
        out = format_str(src, mode=Mode())
    except Exception as exc:
        print("    format_str 抛", type(exc).__name__, str(exc)[:80])
        continue
    print("    输出", repr(out))
    facts(src, "改之前")
    facts(out, "改之后")

for key, src in SAMPLES.items():
    with open("/tmp/bk_ann_%s.py" % key, "w") as fh:
        fh.write(src)
    r = subprocess.run([sys.executable, "-m", "black", "/tmp/bk_ann_%s.py" % key],
                       capture_output=True, text=True)
    tail = (r.stderr.strip().splitlines() or [""])[-1]
    print(key, "black 默认(含 --safe) rc=", r.returncode, "| 末行", tail[:90])
    r2 = subprocess.run([sys.executable, "-m", "black", "--fast", "/tmp/bk_ann_%s.py" % key],
                        capture_output=True, text=True)
    tail2 = (r2.stderr.strip().splitlines() or [""])[-1]
    print(key, "black --fast rc=", r2.returncode, "| 末行", tail2[:90])
