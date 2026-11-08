-- name: Conflict :many
select id from authors where name = sqlc.arg(n) or name = sqlc.narg(n);
