create extension if not exists citext;
create type color as enum ('red', 'green');
create type point2 as (x float8, y float8);
create type shape as (name text, origin point2, colors color[]);
create domain email as citext;

create table everything (
    id serial primary key,
    b bool not null,
    i2 int2 not null,
    i8 int8 not null,
    f4 float4 not null,
    num numeric(10, 2) not null,
    money_ money not null,
    txt text not null,
    vc varchar(10) not null,
    ch char(3) not null,
    raw bytea not null,
    uid uuid not null,
    d date not null,
    t time not null,
    tz timetz not null,
    ts timestamp not null,
    tstz timestamptz not null,
    iv interval not null,
    j json not null,
    jb jsonb not null,
    addr inet not null,
    net cidr not null,
    mac macaddr not null,
    bits bit(3) not null,
    pt point not null,
    ir int4range not null,
    dr daterange not null,
    imr int4multirange not null,
    ints int4[] not null,
    colors color[] not null,
    shapes shape[] not null,
    mail email not null,
    note citext
);

create view everything_names as select id, txt from everything;
create type asset_type as enum (
    'HolidayHome', 'BuyToLetProperty', 'webSite', 'ISDN', 'HTTPServer',
    'Business2', 'CarerOfAChildUnder16', 'in-progress', 'on hold'
);
