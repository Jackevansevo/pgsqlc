-- name: Snap :one
select pg_current_snapshot() as s;
