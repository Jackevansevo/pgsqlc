-- name: CreateBook :one
insert into books (author_id, title, pages, published)
values (@author_id, @title, @pages, @published)
returning *;

-- name: AuthorBooks :many
-- Every author with their books; LEFT JOIN makes all columns nullable.
select a.name as author, b.title
from authors a left join books b on b.author_id = a.id
order by a.id, b.id;

-- name: AuthorBooksInner :many
-- LEFT JOIN that Postgres turns into an inner join.
select a.name as author, b.title
from authors a left join books b on b.author_id = a.id
where b.title = @title;

-- name: BookTitles :many
select title, published from books where author_id = @author_id order by id;

-- name: CreateUser :one
insert into auth.users (email) values (@email) returning *;
