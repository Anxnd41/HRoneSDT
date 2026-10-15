# Implementation decisions

## Indexes

I added a unique index on `employees.emp_code` so employee codes cannot be
duplicated, even when requests arrive at the same time. Attendance records have
a unique compound index on `emp_code` and `date`; this is what guarantees one
record per employee per attendance day. The other indexes support the common
department, date, employee, status, and joined-date filters used by the list
and reporting endpoints.

I did not add indexes for every derived value. The main lookups are by employee
and calendar date, and adding indexes for values such as `late_minutes` would
increase write cost without helping the most important queries.

## Concurrent punch-ins

Both requests first find the employee and calculate the same attendance date.
They then try to insert the record. MongoDB allows only one insert because of
the unique `(emp_code, date)` index. The successful request returns `201`; the
other receives `DuplicateKeyError`, which the API returns as `409`. The
application does not rely on a check-then-insert race-prone decision.

## Leaderboard ties

The leaderboard is sorted by total late minutes and employee code. Tied rows
receive the rank of the first row in that group, so the ranks look like
`1, 2, 2, 4`. The limit is applied after ranking, which means all employees tied
at the cutoff are included.

## Department headcount

The department summary starts with employees who joined by the end of the
requested month. Attendance logs are looked up afterwards. This means an
employee with no logs still contributes to headcount.

## If the data grew significantly

For a much larger installation, I would move the remaining Python-side
aggregation work into MongoDB pipelines and introduce pre-aggregated daily
facts for reports that are requested frequently. The current API and natural
keys could remain the same while the reporting implementation scales
independently.
