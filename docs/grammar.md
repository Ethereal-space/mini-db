# MiniDB 核心 SQL 文法

本文件是 A 成员与独立验收 AI 共用的核心文法快照。只有任务卡明确启用的扩展可以在独立入口接受额外语法；核心 `FrontendPort.parse` 必须持续满足这里的规则。

## 词法约定

```ebnf
name       = ASCII_LETTER_OR_UNDERSCORE
             { ASCII_LETTER_OR_DIGIT_OR_UNDERSCORE } ;
integer    = DIGIT { DIGIT } ;
float      = DIGIT { DIGIT } "." DIGIT { DIGIT } ;
string     = "'" { NON_QUOTE | "''" } "'" ;
line_note  = "--" { NON_NEWLINE } ( NEWLINE | EOF ) ;
block_note = "/*" { ANY_EXCEPT_CLOSING_MARK } "*/" ;
```

- 标识符最长 64 个字符，只接受 ASCII 形式，value 使用 `casefold()` 后的小写名，lexeme 保留原文。
- 关键字大小写不敏感；字符串内容大小写敏感，中文按原值保留。
- 两个单引号 `''` 在字符串内部解码为一个单引号。
- FLOAT 不支持指数；`1.`、`1..2`、`12abc` 作为完整非法数字片段报词法错误，不能拆成看似合法的多个 Token。
- 负号始终先作为独立 Token；核心 Parser 只在 literal 位置把 `- INTEGER` 合成负整数字面量。
- 最长匹配先识别 `>= <= != == <>`，再识别单字符运算符。
- `--` 行注释和不嵌套的 `/* ... */` 块注释不产生 Token，但必须正确推进 Span。字符串中的注释标记只是字符。
- Token 流最后恰好有一个 EOF；EOF Span 是源码末尾的零宽区间。

## 核心 EBNF

```ebnf
program    = { ";" | statement ";" } [ statement ] EOF ;
statement  = create | insert | select | delete ;

create     = CREATE TABLE name "(" coldef { "," coldef } ")" ;
coldef     = name ( INT | VARCHAR ) ;

insert     = INSERT INTO name [ "(" names ")" ]
             VALUES "(" literal { "," literal } ")" ;

select     = SELECT ( "*" | names ) FROM name [ WHERE expr ] ;
delete     = DELETE FROM name [ WHERE expr ] ;

names      = name { "," name } ;

expr       = or_expr ;
or_expr    = and_expr { OR and_expr } ;
and_expr   = not_expr { AND not_expr } ;
not_expr   = NOT not_expr | comparison ;
comparison = primary [ comp_op primary ] ;
primary    = name | literal | "(" expr ")" ;
comp_op    = "=" | "!=" | "<" | "<=" | ">" | ">=" | "==" | "<>" ;
literal    = INTEGER | "-" INTEGER | STRING | FLOAT ;
```

末尾最后一条语句可以没有分号；多条语句之间必须有分号。连续分号表示空语句并跳过。空输入、仅空白、仅注释或仅分号返回空列表。Parser 必须消费到 EOF，不能忽略尾随 Token。

## 表达式调用层级

递归下降函数必须与优先级从低到高对应：

```text
parse_expression
  └─ parse_or
      └─ parse_and
          └─ parse_not
              └─ parse_comparison
                  └─ parse_primary
```

同级 AND、OR 左结合，NOT 右结合。比较最多出现一个运算符；比较层不能用循环形成链式比较。

| SQL | 规范 AST |
| --- | --- |
| `NOT age = 18` | `NOT(=(age,18))` |
| `NOT NOT age = 18` | `NOT(NOT(=(age,18)))` |
| `a = 1 OR b = 2 AND c = 3` | `OR(=(a,1),AND(=(b,2),=(c,3)))` |
| `(a = 1 OR b = 2) AND c = 3` | `AND(OR(=(a,1),=(b,2)),=(c,3))` |
| `a < b < c` | 第二个 `<` 处报 SYNTAX |

`parse_expression` 的代码讲解必须覆盖各层调用、每层消费哪些 Token、如何保证进展、Span 如何合并、EOF/右括号如何停止，以及链式比较为什么被拒绝。测试要断言树形，不能只断言解析成功。

## Parser 与 Semantic 的边界

Parser 只判断结构并构造 AST：

- 重复建表列、未知表、未知列、重复 INSERT 列、缺列和类型不匹配仍可以形成 AST，由 B 报 SEMANTIC；
- CREATE 的列列表与 INSERT 的值列表不能为空，这是语法规则；
- SELECT 的 `*` 不能和其他投影混用；显式投影允许重复且保持原顺序；
- FLOAT 可以形成 Literal，让 B 报核心不支持；FLOAT 列类型不属于核心文法；
- Parser 不查询 Catalog、不分配 table_id/page_id、不访问行，也不计算常量表达式。

## 核心明确拒绝的语法

核心入口拒绝 VARCHAR(n)、多行 VALUES、UPDATE、DISTINCT、表/列别名、限定列名、JOIN、ORDER BY、LIMIT、GROUP BY、HAVING、聚合、NULL、BOOL 列、一般一元/二元算术、EXPLAIN 和事务命令。错误必须停在第一个无法继续的真实 Token，并给出实际种别、期望集合和 Span。

部分 TokenKind 为这些扩展预留，仍不代表核心 Parser 接受它们。扩展必须通过任务卡规定的能力开关和版本化 `ExtensionStatement` 产出；关闭扩展后，相同输入仍按核心规则拒绝。

## 多语句错误行为

核心 parse 是“首错停止”：任何词法或语法错误使整个文本不产生可执行语句。Panic Mode 只属于 A-E08 的独立 `parse_recovering` 入口，恢复出的后续 AST 不自动送入执行器。同步函数必须至少消费一个 Token并移动到分号或 EOF，防止同一错误无限重复。
