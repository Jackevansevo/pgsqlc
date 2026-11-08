-- name: Many :many
select id from authors where id in (sqlc.slice(ids));
