-- name: Both :one
select u.*, (select null::auth_users) as e from auth.users u;

-- name: Users :many
select * from auth.users;
