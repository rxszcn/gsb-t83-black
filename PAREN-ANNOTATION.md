# 模块级带括号 annotated assignment 过 black 后的运行时差异（现状说明）

环境：Black 26.5.1（仓库 `src/black`），CPython 3.14.4，默认 Mode。复现命令：
`PYTHONPATH=src .venv/bin/python repro/annassign.py`。本文只描述现状，不改任何实现或夹具。

## 一、六个样本的读数

`format_str` 的改前/改后文本与 `compile()` 出的内嵌函数（`co_consts` 里带 `co_name`
的 code object）：

| 样本 | 输入 | `format_str` 输出 | 改之前内嵌函数 | 改之后内嵌函数 |
| --- | --- | --- | --- | --- |
| A | `((x)): int = 5` | `x: int = 5` | `[]` | `['__annotate__']` |
| B | `(x): int = 5` | `x: int = 5` | `[]` | `['__annotate__']` |
| C | `(((y))): int = 5` | `y: int = 5` | `[]` | `['__annotate__']` |
| D | `((obj.attr)): int = 5` | `obj.attr: int = 5` | `[]` | `[]` |
| E | `((z)): int`（无右值） | `z: int` | `[]` | `['__annotate__']` |
| F | `((w)) = 5`（普通赋值，无注解） | `w = 5` | `[]` | `[]` |

即：A/B/C/E 删掉括号后从"无 `__annotate__`"变成"有 `__annotate__`"；D（属性目标）
和 F（根本不是 annotated assignment）删括号前后都没有 `__annotate__`。脚本里
`__annotations__` 打成 `None` 是因为它是用普通 dict 当 globals `exec` 的；按模块方式
运行时，A/B/C/E 改后 `module.__annotations__ == {'x': int}`（惰性求值），改前为 `{}`。

两次 black 命令对临时文件 `/tmp/bk_ann_<X>.py` 的退出码与 stderr 末行（先跑默认、再跑
`--fast`）：

| 样本 | 默认命令 rc | 默认末行 | `--fast` rc | `--fast` 末行 |
| --- | --- | --- | --- | --- |
| A | 123 | `1 file failed to reformat.` | 0 | `1 file reformatted.` |
| B | 123 | `1 file failed to reformat.` | 0 | `1 file reformatted.` |
| C | 123 | `1 file failed to reformat.` | 0 | `1 file reformatted.` |
| D | 0 | `1 file reformatted.` | 0 | `1 file left unchanged.` |
| E | 123 | `1 file failed to reformat.` | 0 | `1 file reformatted.` |
| F | 0 | `1 file reformatted.` | 0 | `1 file left unchanged.` |

默认失败样本（A/B/C/E）的 stderr 首行是：
`error: cannot format /tmp/bk_ann_X.py: INTERNAL ERROR: Black 26.5.1 on Python (CPython)
3.14.4 produced code that is not equivalent to the source.  Please report a bug on
https://github.com/psf/black/issues.  This diff might be helpful: /tmp/blk_XXXX.log`。
D/F 没有这行，因为它们的改前改后 AST 本来就一致。D/F 第二遍 `--fast` 显示
`left unchanged` 只是因为默认那遍已经把文件改写成定态文本，第二遍无事可做（
`format_file_contents` 抛 `NothingChanged`），不是 `--fast` 与默认行为不同。

为什么同样是删括号，`--fast` 一律放行：rc=123 不是格式化阶段抛的，而是格式化之后的
AST 等价性安全检查抛的。`src/black/__init__.py:1122`
（`format_file_contents`）里是 `if not fast and not mode.is_ipynb:` 才调用
`check_stability_and_equivalence`；`--fast/--safe` 选项的 help 原文也写明
"By default, Black performs an AST safety check after formatting your code. The --fast
flag turns off this check ... [default: --safe]"
（`src/black/__init__.py:390`）。所以括号照样被删，只是 `--fast` 不再比对改前改后的
AST，自然不会报 rc=123。

## 二、括号在哪一步被去掉，判据读的是什么

去掉括号的是行生成阶段的
`normalize_invisible_parens`（`src/black/linegen.py:1490`）。
`((x)): int = 5` 被 blib2to3 解析成 `expr_stmt: [atom: '(' atom: '(' NAME ')' ')',
annassign: ':' ...]`。遍历 `expr_stmt` 的孩子时，对 index 0 的 `atom` 命中专属分支
（`src/black/linegen.py:1542-1559`，条件是
`index == 0 and child.type == syms.atom and node.type == syms.expr_stmt`
且无 magic trailing comma、不是多行 atom），该分支调用
`maybe_make_parens_invisible_in_atom(child, parent=node, ...,
remove_brackets_around_comma=True, allow_star_expr=True)`，成功后再
`wrap_in_parentheses(node, child, visible=False)`。同一函数对 `syms.annassign` 孩子
还有一处递归（`src/black/linegen.py:1514-1518`），但目标上的括号在 index-0 atom 这一步
就已处理，与该递归无关。

真正决定"能不能扔"的判据在
`maybe_make_parens_invisible_in_atom`（`src/black/linegen.py:1803`）开头的那串
早退条件：`node.type` 必须是 `syms.atom`/`syms.expr`，且不是空/单元素 tuple、不是
as-expr/with 里的 tuple、不是 yield、atom 内最高分隔符优先级不到 COMMA_PRIORITY（
`remove_brackets_around_comma=True` 时这一条被跳过）、不是含 walrus/star 的 tuple、
不是 generator；然后再看 walrus 白名单（`is_walrus_assignment` 时对
`syms.annassign`/`syms.expr_stmt` 等父节点保留）。这串判据读的全是**语法形状**（
括号里是什么表达式、外层是什么语句节点、有没有逗号/星号/walrus），没有任何一条读
Python AST 的 `AnnAssign.simple`，也不关心这个 atom 是不是 annotated assignment 的
目标。六个样本的目标 atom 都只是"包着单个 NAME（D 是 NAME+trailer）的括号"，所有
早退条件都不命中，于是走到 `src/black/linegen.py:1878-1886`：

```python
if is_lpar_token(first) and is_rpar_token(last):
    middle = node.children[1]
    ...
        first.value = ""
        last.value = ""
```

把成对括号叶节点的文本清空（变"隐形括号"），再递归对内层 atom 做同样的事，所以 A/B/C
不论套几层都被剥光。判据里唯一贴近注解语义的只有 walrus 那段的父节点白名单，
NAME 目标的括号不在其保护范围内。

## 三、为什么带括号编译不出 `__annotate__`，不带就出得来；安全检查在哪一句发现

这条规矩是 CPython 定的，源头是 PEP 526 起就存在的
`AnnAssign.simple` 位，3.14 随 PEP 649（惰性注解）改变了它的运行时后果。

PEG 文法在 `Grammar/python.gram` 的 `assignment` 规则里直接写死 simple 位（v3.14.4，
147-161 行）：

- `a=NAME ':' b=expression ...` → `_PyAST_AnnAssign(..., 1, EXTRA)`，`simple=1`；
- `a=('(' b=single_target ')' { b } | single_subscript_attribute_target) ':' ...`
  → `_PyAST_AnnAssign(a, b, c, 0, EXTRA)`，`simple=0`。

即"目标是不是一个没被括号包住的单个 NAME"在解析期就被编码进 AST，与括号层数无关：
`ast.parse` 打出来 A/B/C/E 改前都是 `AnnAssign(... simple=0)`，去掉括号后是
`simple=1`；D 前后都是 `simple=0`（Attribute 目标）。语言参考的原文（
`Doc/reference/simple_stmts.rst`，3.14）：

> The assignment target is considered "simple" if it consists of a single name that is
> not enclosed in parentheses.

> If the assignment target is not simple (an attribute, subscript node, or parenthesized
> name), the annotation is never evaluated.

代码生成端在 `Python/codegen.c` 的 `codegen_annassign`（v3.14.4，5473 行起）：只有
`s->v.AnnAssign.simple && (SCOPE_TYPE(c) == COMPILE_SCOPE_MODULE ||
... COMPILE_SCOPE_CLASS)` 时才调 `_PyCompile_AddDeferredAnnotation` 把这条注解登记进
延迟列表；模块/类作用域收尾时 `codegen_process_deferred_annotations`（790 行）才据此
生成名为 `__annotate__` 的 annotation scope 代码对象（
`codegen_setup_annotations_scope`，668 行）。该函数上方有一句注释正好点名这种情形：

> // It's possible that ste_annotations_block is set but u_deferred_annotations is not,
> // because the former is still set if there are only non-simple annotations (i.e.,
> // annotations for attributes, subscripts, or parenthesized names).

所以带括号的 NAME 目标（simple=0）注解既不进延迟列表、也不产生 `__annotate__` 函数，
注解永不求值；去掉括号（simple=1）后注解进入 `__annotate__`，运行时语义就变了。D 的
Attribute 目标前后都是 simple=0，F 根本不是 AnnAssign，所以二者无差异。3.13 及以前
simple=0 的区别只是"立即求值但不存进 `__annotations__`"，不会改变内嵌 code object 的
有无；3.14 的 PEP 649 让这个位开始决定 `__annotate__` 的生成，这是 Black 的形状判据
当年没考虑到的新后果。

Black 的安全检查在 `assert_equivalent`（`src/black/__init__.py:1633`）发现对不上：
它用 `src/black/parsing.py:143` 的 `parse_ast`（`ast.parse`，3.3 到当前解释器版本取
最新）分别解析改前改后，再用 `stringify_ast`（`src/black/parsing.py:175`）把整棵 AST
逐字段序列化成字符串逐字比较。比对就是 `src/black/__init__.py:1656-1658` 这一句：

```python
if src_ast_str != dst_ast_str:
```

差异落到 `AnnAssign` 的 `simple` 字段（diff 日志原文：`simple= 0,  # int` →
`simple= 1,  # int`），随即在下一句抛出 `ASTSafetyError`（类定义在
`src/black/parsing.py:118`），报错原文抄一行：

```
INTERNAL ERROR: Black 26.5.1 on Python (CPython) 3.14.4 produced code that is not equivalent to the source.  Please report a bug on https://github.com/psf/black/issues.  This diff might be helpful: /tmp/blk_XXXX.log
```

CLI 捕获后打印 `1 file failed to reformat.`（`src/black/report.py:92`）并以内部错误码
123 退出。`assert_stable` 与此无关——文本本身是幂等的，第二遍不会变；是等价性检查先
挂了。

## 四、收口：判据该改在哪一层，哪些夹具会跟着变，选哪边

判据该改在**格式化器的括号决策层**，不是安全检查层，也不是 CPython：

- 不能动 `assert_equivalent`/`stringify_ast`。它逐字段比 AST（含 `simple`）是正确的，
  A/B/C/E 的改前改后在 3.14 下确实不等价；让检查忽略 `simple` 等于放行真实的运行时
  差异，默认命令会"绿着写错代码"。
- 也不该指望解释器：`simple` 是 PEP 526 文法的既有语义、PEP 649 又赋予了惰性求值后果，
  括号承载的是"这条注解永不求值"的用户意图，保留它才是保真。
- 落点应在 `src/black/linegen.py:1542-1559`（`normalize_invisible_parens` 里
  `expr_stmt` 的 index-0 atom 分支）：剥括号前增加一个语法判据——当 `node` 是
  `syms.expr_stmt`、index 0 是 atom、且它右侧紧接 `syms.annassign` 孩子，并且该 atom
  层层剥开后中间只是单个 NAME（而非带 trailer 的属性/下标）时，不对它调用
  `maybe_make_parens_invisible_in_atom`（等价于把这种"annotated assignment 的括号 NAME
  目标"加进保留集合）。判定只用 blib2to3 树形状即可（解析树里 annassign 孩子与
  `atom → '(' (atom → '(' NAME ')') ')'` 结构都可直接点到），不需要引入 `ast`。
  泛化的话，同类守卫对类作用域同样有意义（已实测 class 体里 simple=0/1 一样决定
  `__annotate__` 有无），但语句级这一处对模块级和类级都生效。

夹具影响面（已按 `# output` 分段 + `ast` 全量扫过 `tests/`、`src/`）：当前没有任何
夹具包含"括号包住的简单 NAME + 注解目标"这种形状，`AnnAssign.simple == 0` 且 target
为 `Name` 的用例数为 0，所以改这一处判据，现有 data-case 夹具一个都不用变。唯一名字
相近的是 `tests/data/cases/remove_parens_from_lhs.py`，其中 `(b) = a()[0]` 是**普通
赋值**（expr_stmt 里没有 annassign 孩子），守卫不命中，仍按原样剥成 `b = a()[0]`，
该夹具及其子测试不受影响；属性目标 D 这类（括号包 NAME+trailer）也不命中新守卫，
`((obj.attr)): int` 继续剥成 `obj.attr: int`，与现状一致。后续要补的只是一个新夹具，
把 `(x): int = 5` 固定为"保留括号"的新定态输出。

我的选择：**改格式化器那层、保留用户括号**（即让默认输出维持
`(x): int = 5`，默认命令 rc=0），而不是放松安全检查继续删括号。理由是 3.14 起这对
括号有确定的运行时含义（注解是否进入 `__annotate__`、是否会被求值），Black 的安全
检查没错，错的是剥括号的形状判据少看了"annassign 的简单 NAME 目标"这一维；保留括号
牺牲的只是一点外观整洁，换来默认命令不为用户代码制造语义改动。

## 附：验证记录

- `PYTHONPATH=src .venv/bin/python repro/annassign.py` 输出与上表一致（Python 3.14.4）。
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_black.py -q` 连续两遍：
  均为 `10 failed, 148 passed, 4 subtests passed`，两遍条数与子测试数完全一致。
  10 个失败为基线既有的环境类失败（`TestFileCollection` 的 gitignore/路径用例 9 个、
  `test_false_positive_symlink_output_issue_3384` 1 个），与本次只新增文档无关。
