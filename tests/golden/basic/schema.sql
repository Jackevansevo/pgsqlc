create type mood as enum ('happy', 'sad', 'in-progress');
create type address as (street text, city text, mood mood);
create domain posint as integer check (value > 0);
create schema auth;

create table authors (
    id bigserial primary key,
    name text not null,
    bio text,
    mood mood not null default 'happy',
    tags text[] not null default '{}',
    home address,
    rating numeric
);

create table books (
    id bigserial primary key,
    author_id bigint not null references authors (id),
    title text not null,
    pages posint not null,
    published date,
    meta jsonb not null default '{}'
);

create table auth.users (
    id uuid primary key default gen_random_uuid(),
    email text not null,
    created_at timestamptz not null default now()
);
