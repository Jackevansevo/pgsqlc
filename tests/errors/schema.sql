create schema auth;
create table authors (id bigint primary key, name text not null);
create table auth.users (id bigint primary key);
create type auth_users as enum ('x');
create type clash as enum ('a-b', 'a_b');
create type case_clash as enum ('FooBar', 'foo_bar');
create table no_rows_error (id bigint primary key);
