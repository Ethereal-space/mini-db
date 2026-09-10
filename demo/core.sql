CREATE TABLE student(id INT, name VARCHAR, age INT);
INSERT INTO student(id,name,age) VALUES (1,'Alice',20);
INSERT INTO student(id,name,age) VALUES (2,'Bob',17);
INSERT INTO student(id,name,age) VALUES (3,'Tom',22);
INSERT INTO student(id,name,age) VALUES (4,'张三',21);
SELECT id,name FROM student
WHERE 1 = 1 AND age >= 18 AND NOT (name = 'Tom');
DELETE FROM student WHERE id = 1;
SELECT * FROM student;
