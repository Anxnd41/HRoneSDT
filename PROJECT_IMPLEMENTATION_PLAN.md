# Employee Attendance & Analytics API

## Project Implementation Plan

This project is a FastAPI and MongoDB employee attendance system. The main source of truth is [`openapi.yaml`](./openapi.yaml), especially business rules R1-R10.

The project has three major parts:

1. Review and repair the starter application.
2. Implement the missing attendance and analytics APIs.
3. Add tests, explainability, and documentation.

---

## 1. Understand the project

Important files:

| File | Purpose |
|---|---|
| [`PROBLEM_STATEMENT.docx`](./PROBLEM_STATEMENT.docx) | Assignment requirements and evaluation criteria |
| [`openapi.yaml`](./openapi.yaml) | Complete API contract and business rules |
| [`DATA_MODEL.md`](./DATA_MODEL.md) | MongoDB collections and document structure |
| [`app/main.py`](./app/main.py) | FastAPI application; all application code belongs here |
| [`sample_seed.py`](./sample_seed.py) | Loads sample MongoDB data |
| [`sample_data/`](./sample_data/) | Sample employees and attendance logs |
| [`REVIEW.md`](./REVIEW.md) | Starter-code defect report |
| [`DECISIONS.md`](./DECISIONS.md) | Design decisions and concurrency explanations |

The final application must start with:

```bash
uvicorn app.main:app --port 8000
```

---

## 2. Set up the development environment

### Create a virtual environment

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Start MongoDB

Use local MongoDB or MongoDB Atlas.

Example with Docker:

```bash
docker run -p 27017:27017 mongo:7
```

Configure environment variables:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Do not commit `.env`.

### Load sample data

```bash
python sample_seed.py
```

### Run the API

```bash
uvicorn app.main:app --port 8000 --reload
```

Check that the service is running:

```bash
curl http://localhost:8000/health
```

---

## 3. Add MongoDB indexes during startup

Indexes must be created automatically and idempotently when the application starts.

### Employee indexes

Recommended indexes:

```text
{ emp_code: 1 } unique
{ department: 1, emp_code: 1 }
{ department: 1, joined_on: 1 }
```

These indexes support:

- Unique employee codes.
- Employee filtering and sorting.
- Department headcount calculations.
- Department trend calculations.

### Attendance indexes

Recommended indexes:

```text
{ emp_code: 1, date: 1 } unique
{ date: -1, emp_code: 1 }
{ emp_code: 1, date: 1, status: 1 }
{ date: 1, status: 1 }
```

These indexes support:

- One record per employee and attendance date.
- Employee/date lookups.
- Attendance list sorting and filtering.
- Monthly analytics.
- Status-based queries.

The unique attendance index is required for race-safe punch-ins.

---

## 4. Repair the starter application

Before adding new endpoints, review and fix the existing code in [`app/main.py`](./app/main.py).

### 4.1 Health endpoint

The health endpoint must:

1. Call MongoDB `ping`.
2. Return HTTP `200` and:

```json
{"status": "ok"}
```

when MongoDB responds.
3. Return HTTP `503` when MongoDB is unavailable.

It must not report the service as healthy when the database is down.

### 4.2 Pagination

The correct pagination offset is:

```python
skip = (page - 1) * page_size
```

Validate:

- `page >= 1`
- `1 <= page_size <= 100`

The `total` value must always reflect the active filters.

### 4.3 Employee creation

Validate according to [`openapi.yaml`](./openapi.yaml):

- `emp_code` must match `EMP` followed by 4-6 digits.
- `name` must not be empty and must not exceed 100 characters.
- `email` must have a valid format.
- `department` must not be empty and must not exceed 50 characters.
- `joined_on` must be a valid `YYYY-MM-DD` date.
- `shift_start` and `shift_end` must be valid `HH:MM` values.
- `shift_start` and `shift_end` must differ.

Use the unique database index for duplicate protection. Do not depend only on a pre-insert lookup.

If MongoDB raises a duplicate-key error, return HTTP `409`.

### 4.4 Employee listing

`GET /employees` must:

- Filter by exact, case-sensitive department.
- Sort by `emp_code` ascending.
- Return the filtered total.
- Exclude MongoDB `_id`.
- Return `created_at` as epoch milliseconds.
- Apply correct pagination.

### 4.5 Timestamp handling

All API timestamps must be integer epoch milliseconds.

Validate that timestamps are:

- Integers.
- At least `100000000000`.
- At most `4102444800000`.
- Not strings.
- Not floats.
- Not seconds-based timestamps.

Before calculation and storage, truncate timestamps to whole seconds.

Convert API timestamps to BSON datetimes for MongoDB and convert BSON datetimes back to epoch milliseconds in responses.

This applies to:

- `punched_at`
- `punch_in`
- `punch_out`
- `created_at`
- `history[].at`
- `history[].changes[].from`
- `history[].changes[].to`

### 4.6 Attendance listing

`GET /attendance` must use MongoDB filtering, sorting, skipping, and limiting. It must not load the entire collection into Python.

Required sort order:

```text
date descending, then emp_code ascending
```

Supported filters:

- `emp_code`
- `date_from`
- `date_to`
- `status`

`date_from` and `date_to` are inclusive. Return `422` if `date_from > date_to`.

---

## 5. Add shared helper functions

Keep all code in [`app/main.py`](./app/main.py), but organize it into sections.

Useful helpers include:

```text
parse_epoch_millis
datetime_to_epoch_millis
truncate_to_second
parse_calendar_date
parse_month
parse_shift_time
get_ist_datetime
get_shift_start_datetime
get_shift_end_datetime
calculate_attendance_date
calculate_late_minutes
calculate_work_hours
calculate_overtime_minutes
calculate_half_day
serialize_employee
serialize_attendance
serialize_history
```

Important calculation rules:

- Use IST for shift comparisons.
- Use whole-minute floor calculations.
- Use `Decimal` with `ROUND_HALF_UP` for reported values.
- Do not use Python floating-point `round()` for business calculations.
- Analytics must use stored derived fields rather than recomputing them.
- New records and corrections must calculate and store derived fields.

---

## 6. Implement punch-in

Endpoint:

```text
POST /attendance/punch-in
```

Implementation steps:

1. Find the employee by `emp_code`.
2. Return `404` if the employee does not exist.
3. Resolve `punched_at`; use the current time if it is omitted.
4. Validate and truncate the timestamp.
5. Convert it to IST.
6. Calculate the attendance date:
   - Normal shift: use the IST calendar date.
   - Overnight shift: if the punch-in time is earlier than `shift_end`, use the previous calendar date.
7. Calculate `late_minutes`.
8. Create the attendance record.
9. Let the unique `{emp_code, date}` index protect against concurrent duplicates.
10. Convert duplicate-key errors to HTTP `409`.
11. Return the attendance record without `_id` or an artificial `id`.

Initial values:

```text
punch_out: null
work_hours: null
overtime_minutes: 0
half_day: false
history: []
```

Accepted punch-in statuses:

```text
PRESENT
WFH
ON_DUTY
```

---

## 7. Implement punch-out

Endpoint:

```text
POST /attendance/punch-out
```

Implementation steps:

1. Validate that the employee exists.
2. Resolve and validate `punched_at`.
3. Find the most recent open record where:
   - `emp_code` matches.
   - `punch_in <= punched_at`.
   - `punch_out` is `null`.
4. Sort matching records by `punch_in` descending.
5. Return `404` if there is no matching punch-in.
6. Reject a timestamp that is:
   - Before or equal to `punch_in`.
   - More than 24 hours after `punch_in`.
7. Calculate:
   - `work_hours`
   - `overtime_minutes`
   - `half_day`
8. Atomically update the record only if `punch_out` is still `null`.
9. Return `409` if another concurrent request already closed the record.

Use an atomic update such as:

```text
find_one_and_update(
    {"_id": record_id, "punch_out": None},
    {"$set": {...}}
)
```

Punch-out must work across midnight for overnight shifts.

---

## 8. Implement regularization

Endpoint:

```text
PATCH /attendance/{emp_code}/{date}
```

### Validation rules

1. The employee must exist.
2. The attendance record must exist.
3. `reason` is required and must be 5-200 characters.
4. `regularized_by` is required and must be 1-50 characters.
5. Only `status`, `punch_in`, and `punch_out` can be changed.
6. Derived fields must never be accepted as client-authoritative values.
7. `ABSENT` and `LEAVE` must have both punch times set to `null`.
8. Presence statuses require `punch_in`.
9. `punch_in` must belong to the record’s attendance date according to R1.
10. `punch_out` must be after `punch_in`.
11. `punch_out` must be within 24 hours of `punch_in`.
12. Return `422` if the request changes nothing.

### Update steps

1. Load the existing record.
2. Build the proposed final state.
3. Validate the final state.
4. Recalculate all derived fields.
5. Compare old and new values.
6. Build a history entry containing only changed fields.
7. Append exactly one history item.
8. Use an atomic update to avoid lost concurrent history entries.
9. Return `409` if another regularization changes the record first.

History example:

```json
{
  "at": 1783312500000,
  "by": "hr.admin",
  "reason": "biometric glitch",
  "changes": {
    "punch_in": {
      "from": 1783312500000,
      "to": 1783310280000
    }
  }
}
```

Punch-in and punch-out must never add history entries.

---

## 9. Implement employee monthly analytics

Endpoint:

```text
GET /analytics/employees/{emp_code}/monthly?month=YYYY-MM
```

This must use a MongoDB aggregation pipeline.

Pipeline requirements:

1. Validate the month.
2. Return `404` for an unknown employee.
3. Calculate the month boundaries.
4. Count Monday-Friday working days.
5. Start working-day counting from the later of:
   - Employee `joined_on`.
   - First day of the month.
6. Filter logs by employee and month.
7. Calculate:
   - `present_days`
   - `leave_days`
   - `late_count`
   - `total_late_minutes`
   - `total_overtime_minutes`
8. Count half-days as `0.5`.
9. Count present days only on weekdays.
10. Include weekend records in late and overtime totals.
11. Calculate `attendance_pct`.
12. Use half-up rounding.

---

## 10. Implement department summary

Endpoint:

```text
GET /analytics/departments/summary?month=YYYY-MM
```

Optional filter:

```text
department
```

Start the aggregation from the `employees` collection. This is important because employees with no attendance logs must still be counted.

Pipeline outline:

1. Match employees whose `joined_on` is on or before the last day of the month.
2. Apply the optional department filter.
3. Look up matching attendance logs.
4. Restrict logs to the requested month.
5. Calculate:
   - `headcount`
   - `present_days`
   - `avg_work_hours`
   - `late_count`
   - `total_late_minutes`
   - `leave_count`
   - `on_duty_count`
6. Group by department.
7. Remove departments with zero headcount.
8. Sort by department ascending.
9. Apply correct rounding.

`avg_work_hours` must be the average of eligible attendance records, not an average of employee averages.

---

## 11. Implement the late leaderboard

Endpoint:

```text
GET /analytics/leaderboard/late?month=YYYY-MM
```

Optional parameters:

```text
limit
department
```

Pipeline outline:

1. Filter attendance logs to the requested month.
2. Group by employee.
3. Calculate total late minutes and late record count.
4. Remove employees whose total late minutes are zero.
5. Join employee name and department.
6. Apply the department filter.
7. Sort by:
   - Total late minutes descending.
   - Employee code ascending.
8. Use `$setWindowFields` with standard competition ranking.
9. Filter after ranking so all employees tied at the cutoff are returned.

Example ranks:

```text
1, 2, 2, 4
```

If `limit=2`, return all rows ranked 1 or 2, even if that produces more than two rows.

Do not use the array position as the rank.

---

## 12. Implement department trend

Endpoint:

```text
GET /analytics/departments/{department}/trend?from=YYYY-MM-DD&to=YYYY-MM-DD
```

### Validation

- Return `404` if the department has no employees.
- Return `422` if `to < from`.
- Return `422` if the range exceeds 92 days.
- Return exactly one row for every day in the requested range.

### Pipeline outline

1. Verify the department exists.
2. Generate the complete date range in MongoDB.
3. Include weekends and days without logs.
4. Calculate employee headcount where `joined_on <= date`.
5. Look up department attendance records for each date.
6. Calculate:
   - `is_working_day`
   - `headcount`
   - `present_count`
   - `late_count`
   - `attendance_rate`
7. Use `$setWindowFields` to calculate the seven-day moving average.
8. Keep the moving window inside the requested range.
9. Sort by date ascending.

Rules:

- Present statuses are `PRESENT`, `WFH`, and `ON_DUTY`.
- A half-day contributes `0.5`.
- Working days with no records have rate `0.0`.
- Weekends have rate `null`.
- A working day with zero headcount has rate `null`.
- Moving averages ignore null rates.
- Rates use four-decimal half-up rounding.

---

## 13. Implement the explain endpoint

Endpoint:

```text
GET /admin/explain/{endpoint}
```

Supported endpoint names:

```text
attendance_list
employee_monthly
department_summary
late_leaderboard
department_trend
```

The endpoint must:

1. Validate the same parameters as the real endpoint.
2. Build the same query or aggregation pipeline.
3. Use MongoDB `explain` with `executionStats`.
4. Return the raw explain result.

Response shape:

```json
{
  "endpoint": "attendance_list",
  "collection": "attendance_logs",
  "explain": {}
}
```

The explain plan must contain:

```text
IXSCAN
```

and must not contain:

```text
COLLSCAN
```

Reuse query-building helpers instead of creating simplified queries only for the explain endpoint.

---

## 14. Create tests

Build tests for the following cases.

### CRUD and validation

- Health success.
- Health failure when MongoDB is unavailable.
- Employee creation.
- Duplicate employee creation.
- Invalid employee code.
- Invalid email.
- Invalid shift.
- Employee filtering.
- Employee pagination.
- Correct totals.

### Time calculations

- Valid epoch milliseconds.
- Seconds rejected.
- Floats rejected.
- String timestamps rejected.
- Out-of-range timestamps rejected.
- Timestamp truncation.
- IST date conversion.
- Overnight shifts.
- Punch-in before overnight shift end.
- Grace period exactly 10 minutes.
- Late by 10 minutes and 1 second.
- Overtime below 30 minutes.
- Overtime exactly 30 minutes.
- Half-day threshold at 4.50 hours.
- Half-up work-hour rounding.

### Concurrency

- Simultaneous punch-ins for the same employee/date.
- Simultaneous punch-outs for the same record.
- Simultaneous regularizations for the same record.

Expected behavior:

- Exactly one concurrent punch-in returns `201`; the rest return `409`.
- Exactly one concurrent punch-out returns `200`; the rest return `409`.
- Concurrent regularizations must not silently lose history entries.

### Analytics

Test data should include:

- Employees with no logs.
- Mid-month joiners.
- Weekend records.
- Missing weekdays.
- Leave records.
- Absent records.
- Half-days.
- Overnight shifts.
- Leaderboard ties.
- Ties at the leaderboard limit.
- Unknown employees.
- Unknown departments.
- Trend ranges with weekends and empty days.
- Ranges longer than 92 days.

### Performance

With approximately 100,000 attendance records, verify:

- Attendance listing does not load all records into Python.
- Required indexes are used.
- Explain output contains `IXSCAN`.
- Explain output does not contain `COLLSCAN`.

---

## 15. Complete the documentation

### Complete `REVIEW.md`

Record every starter defect with:

- Function or line.
- What is wrong.
- How to reproduce it.
- How it was fixed.

Likely defects include:

- Health does not ping MongoDB.
- Incorrect pagination offset.
- Total count ignores filters.
- Duplicate creation is not race-safe.
- Unknown employees are not handled correctly.
- Naive datetime handling.
- Missing epoch-millisecond validation.
- Missing unique indexes.
- Incorrect response serialization.
- In-memory attendance sorting.
- Missing status validation.
- Missing punch-out endpoint.
- Missing regularization endpoint.
- Missing analytics endpoints.
- Missing explain endpoint.

Also document behavior that was inspected and intentionally retained.

### Complete `DECISIONS.md`

Answer these five questions in approximately 150-200 words:

1. Which indexes were created and what does each support?
2. What happens when two identical punch-ins arrive simultaneously?
3. How are leaderboard ties handled at the cutoff?
4. How does department summary include employees with no logs?
5. What would change if the application had 100 times more data?

### Update `README.md`

Document:

- Python version.
- Installation steps.
- MongoDB setup.
- Environment variables.
- Sample data loading.
- API startup command.
- Test commands.
- Known limitations.

Do not commit:

- `.env`
- Secrets.
- Dockerfile.
- Virtual environments.
- Database dumps.

---

## 16. Recommended implementation order

Implement in this order:

1. Add Pydantic request models and validation.
2. Add timestamp, date, shift, and rounding helpers.
3. Add response serialization helpers.
4. Add startup index creation.
5. Fix the health endpoint.
6. Fix employee creation and listing.
7. Fix punch-in.
8. Implement punch-out.
9. Implement regularization.
10. Fix attendance listing.
11. Implement employee monthly analytics.
12. Implement department summary.
13. Implement the late leaderboard.
14. Implement department trend.
15. Implement the explain endpoint.
16. Complete `REVIEW.md`.
17. Complete `DECISIONS.md`.
18. Update `README.md`.
19. Run functional, concurrency, analytics, and performance tests.
20. Verify the clean-clone submission process.

---

## 17. Final acceptance checklist

- [ ] `uvicorn app.main:app --port 8000` starts successfully.
- [ ] `/health` returns `503` when MongoDB is unavailable.
- [ ] All indexes are created automatically.
- [ ] Employee codes are unique under concurrency.
- [ ] Attendance records are unique by `(emp_code, date)`.
- [ ] API timestamps use epoch milliseconds.
- [ ] Timestamp calculations use whole seconds.
- [ ] IST and overnight shifts work correctly.
- [ ] Derived fields are calculated and serialized correctly.
- [ ] Analytics use MongoDB aggregation pipelines.
- [ ] Attendance listing does not load the complete collection into Python.
- [ ] Headcount includes employees with no logs.
- [ ] Leaderboard ties use standard competition ranking.
- [ ] Trend output contains every requested day.
- [ ] Moving averages are limited to the requested range.
- [ ] Explain output demonstrates index usage.
- [ ] `REVIEW.md` is complete.
- [ ] `DECISIONS.md` is complete.
- [ ] `README.md` is complete.
- [ ] No `.env`, secrets, Dockerfile, virtual environment, or data dump is committed.
