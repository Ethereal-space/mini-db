# MiniDB 核心前端文法

来源：四人实施手册“核心文法与可观察行为”、A-B01 至 A-B06。该文件是本次缺失基线初始化时的文法快照，后续个人开发应在 walkthrough 记录对照，不擅自改公共文法。

```ebnf
program    = { ";" | statement ";" } [ statement ] EOF
statement  = create | insert | select | delete
create     = CREATE TABLE name "(" coldef {"," coldef} ")"
coldef     = name (INT | VARCHAR)
insert     = INSERT INTO name ["(" names ")"]
             VALUES "(" literal {"," literal} ")"
select     = SELECT ("*" | names) FROM name [WHERE expr]
delete     = DELETE FROM name [WHERE expr]
names      = name {"," name}
expr       = or_expr
or_expr    = and_expr { OR and_expr }
and_expr   = not_expr { AND not_expr }
not_expr   = NOT not_expr | comparison
comparison = primary [comp_op primary]
primary    = name | literal | "(" expr ")"
comp_op    = "=" | "!=" | "<" | "<=" | ">" | ">=" | "==" | "<>"
literal    = INTEGER | "-" INTEGER | STRING | FLOAT
name       = IDENTIFIER
```

## 词法规则

- 标识符为 `[A-Za-z_][A-Za-z0-9_]*`，最多 64 个字符。原文保存在 lexeme，value/name 为小写。关键字也以 casefold 规范化，列类型 dtype_name 则为 `INT` 或 `VARCHAR`。
- INTEGER 为 ASCII 数字串。FLOAT 为 `[0-9]+\.[0-9]+`；无指数、无省略小数部分。`1.`、`1..2`、`12abc`、`1.2.3` 作为非法数字拒绝。负号是独立 MINUS Token。
- STRING 使用单引号，`''` 表示值中的一个单引号。允许中文、空串和真实换行；CRLF 原样保留在值中。反斜杠不作转义。
- `--` 到换行或 EOF；`/* */` 不嵌套。注释不产生 Token，字符串中的注释符无特殊含义。
- 双字符符号先于单字符匹配。`==` 与 `<>` 保留原词素及独立种别，value 与 AST op 分别规范为 `=` 和 `!=`。
- EOF 恰好一个、零宽、value=None。普通空白不产生 Token。
- 额外保留关键字见 `lexer.KEYWORDS` 的扩展关键字组，以 `UNSUPPORTED_KEYWORD` 表示：它们不会启用扩展。例如 `NULL`、`FLOAT` 类型、`UPDATE`、`JOIN`、`ORDER`、`LIMIT` 在核心对应位置报语法错误。`FLOAT` 小数字面量和 `FLOAT` 类型关键字是不同的 Token kind。

## 结构与语义边界

比较在 NOT 内部结合，随后 AND，最后 OR。`NOT a=1 OR b=2 AND c=3` 的树为 `OR(NOT(=(a,1)), AND(=(b,2), =(c,3)))`。OR/AND 同级左结合；NOT 右结合。`a<b<c` 在第二个比较符报错。

最后一条语句可无分号，中间分号必需，连续空分号跳过。尾随未知 Token 必须报错，不能返回部分成功。

CREATE 至少一列；INSERT 至少一个值，省略列列表用 None。列列表保留原序和重复项。SELECT 星号用 None，显式列列表用 tuple，不能混合 `*,id`。DELETE 无 WHERE 时 where=None。

语义问题留给 B：未知表列、重复列、缺列、列值数量不一致、数值范围、字符串字节长度、类型和 WHERE 是否 BOOL。FLOAT 数值允许进入 Literal。核心不支持 `VARCHAR(n)`、多行 VALUES、一般算术或负浮点字面量。

## 源码位置和运行资源

offset 从零按 Unicode 码点计数，行列一基，Span 为 `[start,end)`。CRLF 消耗两个码点但只换一行；单独 CR/LF 也换行；Tab 增加一列。语句 Span 不含分号及尾部空白。括号不生成独立节点，但包裹的根表达式 Span 覆盖括号；内部节点仍保留自己的位置。

为避免用户输入耗尽 Python 调用栈，本实现把括号嵌套限制为 64 层，超过时报带真实位置的 `SYNTAX/NESTING_LIMIT`。这是实现资源上限，不是手册文法要求。长 NOT、AND/OR 链及格式化使用显式循环/栈，不受该括号限制。小数超过有限 Python float 范围时报 `LEXICAL/INVALID_NUMBER`，不生成无法标准 JSON 表示的 Infinity。任意长整数不在 A 做 INT32 检查。
