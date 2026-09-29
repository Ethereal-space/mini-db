-- 成员 B SQL 编译器验收脚本
-- 在 MiniDB Studio 的 SQL 工作台中导入后执行

CREATE TABLE student(id INT, name VARCHAR, age INT);

INSERT INTO student(id, name, age) VALUES (1, 'Alice', 20);
INSERT INTO student(id, name, age) VALUES (2, 'Bob', 17);
INSERT INTO student(id, name, age) VALUES (3, 'Tom', 22);
INSERT INTO student(id, name, age) VALUES (4, '张三', 21);

SELECT id, name
FROM student
WHERE age >= 18 AND NOT (name = 'Alice');

SELECT name, name, age
FROM student;

DELETE FROM student
WHERE id = 2;

SELECT * FROM student;
