-- name: InsertEverything :one
insert into everything (
    b, i2, i8, f4, num, money_, txt, vc, ch, raw, uid, d, t, tz, ts, tstz, iv,
    j, jb, addr, net, mac, bits, pt, ir, dr, imr, ints, colors, shapes,
    mail, note
) values (
    @b, @i2, @i8, @f4, @num, @money_, @txt, @vc, @ch, @raw, @uid, @d, @t, @tz,
    @ts, @tstz, @iv, @j, @jb, @addr, @net, @mac, @bits, @pt, @ir, @dr, @imr,
    @ints, @colors, @shapes, @mail, sqlc.narg(note)
)
returning *;

-- name: GetShapes :one
select shapes, colors from everything where id = @id;

-- name: Computed :one
select count(*) as n, max(txt) as top, now() as at, pg_sleep(0) as nothing
from everything;

-- name: Rollup :many
select txt, count(*) as n from everything group by rollup (txt) order by txt;

-- name: Positional :many
select id, txt from everything where id = $1 or txt = $2::text;

-- name: Renamed :many
-- Aliasing a column means the table model is not reused.
select id, txt as label from everything;

-- name: ViewRows :many
select * from everything_names;

-- name: Subquery :many
select x.txt from (select txt from everything) x;

-- name: CteRows :many
with picked as (select id, txt from everything where b) select id, txt from picked;

-- name: LiteralStrings :one
-- '@notaparam' and $1 inside literals and comments are left alone.
select '@notaparam $1' as lit, $$sqlc.arg(x)$$ as dollar, @real::text as real -- @nope
;

-- name: AnyAsset :one
-- Enum labels in PascalCase/camelCase are split into words for member names.
select 'HolidayHome'::asset_type as t;
