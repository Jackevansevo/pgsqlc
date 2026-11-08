-- name: InsertRecord :exec
insert into record (id, date) values (@id, @date);

-- name: RecordsOn :many
select * from record where date = @date order by id;
