-- name: Mixed :many
select id from authors where id = @id or name = $2;
