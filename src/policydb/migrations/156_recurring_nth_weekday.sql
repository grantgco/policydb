-- 156: Extend recurring_events with "nth weekday of month" pattern support.
--
-- Before this migration a Monthly/Quarterly/etc. schedule could only repeat on
-- a fixed day-of-month (e.g. "the 15th"). This migration adds an alternative
-- month_pattern='nth_weekday' that expresses rules like "the last Monday of
-- every month" or "the 2nd Tuesday of every quarter".
--
--   month_pattern    : NULL | 'day_of_month' | 'nth_weekday'
--                      NULL is treated as 'day_of_month' for backward compat.
--   nth_weekday_n    : 1..4 for first..fourth, -1 for "last"
--   nth_weekday_dow  : 0=Mon .. 6=Sun (matches existing day_of_week convention)
--
-- Only consulted when cadence is Monthly/Quarterly/Semi-Annual/Annual AND
-- month_pattern = 'nth_weekday'. Weekly/Biweekly continue to use day_of_week.

ALTER TABLE recurring_events ADD COLUMN month_pattern TEXT;
ALTER TABLE recurring_events ADD COLUMN nth_weekday_n INTEGER;
ALTER TABLE recurring_events ADD COLUMN nth_weekday_dow INTEGER;
