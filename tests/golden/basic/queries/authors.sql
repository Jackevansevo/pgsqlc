-- name: GetAuthor :one
-- Fetch one author by primary key.
select id, name, bio, mood, tags, home, rating from authors where id = @id;

-- name: RequireAuthor :single
-- Like GetAuthor, but the author must exist.
select * from authors where id = @id;

-- name: ListAuthors :many
select * from authors order by id;

-- name: CreateAuthor :single
insert into authors (name, bio, mood, tags, home)
values (sqlc.arg(name), sqlc.narg(bio), @mood, @tags, sqlc.narg('home'))
returning *;

-- name: RenameAuthor :execrows
update authors set name = @name where id = @id;

-- name: DeleteAuthor :exec
delete from authors where id = $1;

-- name: AuthorsByMoods :many
select id, name from authors where mood = any(@moods::mood[]) order by id;

-- name: CountAuthors :single
select count(*) as n from authors;
