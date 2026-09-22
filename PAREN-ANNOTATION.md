# 模块级带括号注解赋值过 black 后运行时行为变化（现状说明）

复现：`PYTHONPATH=src .venv/bin/python repro/annassign.py`
环境：black 26.5.1（本仓库 `src/`），CPython 3.14.4（`.venv`）。

## 一、六个样本的改前改后与两条命令的退出码

| 样本 | 输入 | black 输出 | 改前编译 | 改后编译 | 默认命令 | --fast |
|---|---|---|---|---|---|---|
| A | `((x)): int = 5` | `x: int = 5` | 无内嵌函数，`__annotations__` 为 None | 多出内嵌函数 `__annotate__` | rc=123 | rc=0 |
| B | `(x): int = 5` | `x: int = 5` | 同上 | 同上 | rc=123 | rc=0 |
| C | `(((y))): int = 5` | `y: int = 5` | 同上 | 同上 | rc=123 | rc=0 |
| D | `((obj.attr)): int = 5` | `obj.attr: int = 5` | 无内嵌函数 | 无内嵌函数（不变） | rc=0 | rc=0 |
| E | `((z)): int` | `z: int` | 无内嵌函数 | 多出 `__annotate__` | rc=123 | rc=0 |
| F | `((w)) = 5` | `w = 5` | 无内嵌函数 | 无内嵌函数（不变） | rc=0 | rc=0 |

- 默认命令（含安全检查）rc=123 的是 A、B、C、E，stderr 末行均为 `1 file failed to reformat.`（其上有 `Oh no! 💥 💔 💥` 和一条 `error: cannot format ... INTERNAL ERROR ...`）。
- 默认命令 rc=0 的是 D、F，末行 `1 file reformatted.`。
- `--fast` 下六个全部 rc=0：A、B、C、E 末行 `1 file reformatted.`；D、F 末行 `1 file left unchanged.`（这是脚本顺序造成的：默认命令先把文件原地改写了，`--fast` 再跑时文件已是格式化结果）。

为什么同样删括号 `--fast` 就放行：`format_file_contents` 里安全检查被 `fast` 开关整体跳过——`src/black/__init__.py:1122` 是 `if not fast and not mode.is_ipynb: check_stability_and_equivalence(...)`（notebook 路径同理，`src/black/__init__.py:1155`）。`--fast` 时括号照样删、输出一模一样，只是不再比对改前改后的 AST，所以拿不到 rc=123。

## 二、括号在哪一步被去掉、判据读的是什么

链路：`LineGenerator.visit_stmt`（`src/black/linegen.py:225`）对每个语句调 `normalize_invisible_parens`（`src/black/linegen.py:1490`）。`((x)): int = 5` 的解析树是 `simple_stmt → expr_stmt → [atom("((x))"), annassign(": int = 5")]`，于是命中 `normalize_invisible_parens` 里的这个分支（`src/black/linegen.py:1543-1562`）：

```python
if (
    index == 0
    and isinstance(child, Node)
    and child.type == syms.atom
    and node.type == syms.expr_stmt
    and not _atom_has_magic_trailing_comma(child, mode)
    and not _is_atom_multiline(child)
):
    if maybe_make_parens_invisible_in_atom(child, parent=node, ...):
        wrap_in_parentheses(node, child, visible=False)
```

真正决定"这层括号能不能扔"的判据在 `maybe_make_parens_invisible_in_atom`（`src/black/linegen.py:1803`）开头的排除清单（`src/black/linegen.py:1817-1855`）。它读的全是 atom 自身的内容：是不是空元组（`is_empty_tuple`）、单元素元组（`is_one_tuple`）、asexpr_test/with 里的元组、yield、`max_delimiter_priority_in_atom(node) >= COMMA_PRIORITY`（里面有没有逗号）、含不含海象符（`is_tuple_containing_walrus`）、含不含星号解包（`is_tuple_containing_star`）、是不是生成器（`is_generator`）。**它从不读"这个 atom 是不是 annassign 的注解目标"**——`(x)`、`((x))` 这种纯名字 atom 一项都不命中，于是走到 `src/black/linegen.py:1877-1884`：`first.value = ""`、`last.value = ""` 把首尾圆括号叶子掏空（变成"不可见括号"），再递归处理中间节点；`src/black/linegen.py:1891-1907` 顺手把嵌套的不可见括号 atom 整个剥掉。输出时括号叶子值为空串，`((x))` 就变成 `x`。

## 三、为什么带括号编译不出 `__annotate__`、不带就有；安全检查在哪句发现对不上

这条规矩是 CPython 定的，落在 AST 的 `AnnAssign.simple` 字段上：目标是**不带括号的裸名字**时 `simple=1`，名字只要被括号包住（或目标是属性/下标）就是 `simple=0`。实测本环境：`ast.parse('x: int = 5').body[0].simple == 1`，而 `'(x): int = 5'`、`'((x)): int = 5'`、`'(obj.attr): int = 5'` 全是 0。运行时的后果：模块级只有 `simple=1` 的注解会被记进 `__annotations__`；3.14（PEP 649/749）下编译器只为会被记录的注解生成 `__annotate__` 函数。所以改前（带括号，`simple=0`）编译产物里没有 `__annotate__`、执行后 `__annotations__` 仍是 None；black 删掉括号后 `simple` 变 1，`__annotate__` 就出现了。D 不变是因为属性目标本来 `simple` 就是 0；F 不变是因为普通赋值根本没有注解。

black 的安全检查在 `assert_equivalent`（`src/black/__init__.py:1633`）：它用 `stringify_ast`（`src/black/parsing.py:175`，按 `sorted(node._fields)` 逐字段序列化，`simple` 也在其中）分别展开改前改后的 AST，在 `src/black/__init__.py:1657` 的 `if src_ast_str != dst_ast_str:` 这一句发现对不上（diff 里就是 AnnAssign 的 `simple=0` 变成 `simple=1`），然后抛 `ASTSafetyError`（定义在 `src/black/parsing.py:119`）。报错原文一行：

```
error: cannot format /tmp/bk_A.py: INTERNAL ERROR: Black 26.5.1 on Python (CPython) 3.14.4 produced code that is not equivalent to the source.  Please report a bug on https://github.com/psf/black/issues.  This diff might be helpful: /tmp/blk_woohoymt.log
```

## 四、收口：判据该改在哪一层、动了哪些夹具会变、选哪边

要既不丢用户写的括号、又不让默认命令一直红，判据只能改在 **linegen 层**，具体是 `normalize_invisible_parens` 里 `src/black/linegen.py:1543-1562` 这个调用点：在 `node.type == syms.expr_stmt` 之外再加一条"该 expr_stmt 的另一个孩子是 annassign 就不动它"（即注解目标不做去括号）。理由：

- 不能改共享的 `maybe_make_parens_invisible_in_atom`（`src/black/linegen.py:1803`）：它有约十处调用点（return 注解、字典值、tname、with、for、del 等），在里面加 annassign 感知会波及所有路径。
- 更不能改安全层：让 `assert_equivalent`/`_stringify_ast` 忽略 `AnnAssign.simple` 等于给"运行时记不记注解变了"盖章放行，检查形同虚设，方向反了。

夹具影响：按"仅 annassign 目标豁免"改，现有夹具**零变化**——`tests/data/` 里没有任何一个夹具含带括号的注解目标（`rg` 全目录无命中），唯一走同一个调用点的夹具是 `tests/data/cases/remove_parens_from_lhs.py`（`(b) = a()[0]` → `b = a()[0]`），它是普通赋值不是 annassign，继续放行。反过来如果把整个调用点废掉，这份夹具的期望输出就会跟着翻。所以选：在 `linegen.py:1543` 的调用点加 annassign 豁免，夹具不动、默认命令变绿、用户的括号原样保留（A/B/C/E 变成"已格式化，无需改动"）。

## 验证

两遍全量测试读数一致：

```
$ PYTHONPATH=src .venv/bin/python -m pytest tests/test_black.py -q   # 第 1 遍
158 passed, 4 subtests passed in 4.52s
$ PYTHONPATH=src .venv/bin/python -m pytest tests/test_black.py -q   # 第 2 遍
158 passed, 4 subtests passed in 4.14s
```

两遍均为 158 条通过、4 个子测试通过，条数与子测试数完全对上。本次未改动任何实现或夹具文件。
