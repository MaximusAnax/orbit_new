-- Add concise event title (summary remains the long form)

alter table event add column if not exists title text;
