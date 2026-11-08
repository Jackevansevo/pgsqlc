-- name: Reserved :one
select id from authors where id = @conn;
