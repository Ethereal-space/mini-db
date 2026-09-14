UPDATE student SET age=21,name='李四' WHERE id=1;
SELECT id FROM student WHERE age>=18 ORDER BY age DESC,id LIMIT 2;
SELECT DISTINCT name,age FROM student WHERE age>=18;
