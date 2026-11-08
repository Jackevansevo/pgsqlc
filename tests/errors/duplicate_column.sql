-- name: Dup :many
select a.id, b.id from authors a join authors b on true;
