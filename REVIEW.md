# Review of the starter application

I reviewed the original `app/main.py` before filling in the missing endpoints.
The main problems were not limited to missing features; a few of the existing
happy-path implementations would also produce incorrect results for filtered
queries, time-zone boundaries, and concurrent requests.

| # | Area | Problem | How I reproduced it | Change made |
| --- | --- | --- | --- | --- |
| 1 | `health` | The endpoint returned success without checking MongoDB. | Stop MongoDB and call `/health`; it still returned `200`. | Added a MongoDB `ping` and return `503` when it fails. |
| 2 | Employee pagination | The offset used `page * page_size`, which skipped the first page. | Request page 1 with a small page size. | Changed the offset to `(page - 1) * page_size`. |
| 3 | Employee totals | The total count ignored the department filter. | Compare `/employees?department=...` with the unfiltered total. | Count the same filtered query used for the page. |
| 4 | Time handling | Datetimes were created in local time and timestamps were not validated consistently. | Try seconds, floats, strings, and timestamps around an IST date boundary. | Added epoch-millisecond validation, whole-second truncation, UTC storage, and IST calculations. |
| 5 | Rounding | Python float rounding was used for values that require half-up rounding. | Use a duration that lands exactly on a half-cent boundary. | Use `Decimal` with `ROUND_HALF_UP` for reported values. |
| 6 | Employee creation | A read-before-insert check was not enough to prevent duplicate employees under concurrency. | Send two creates for the same employee code at the same time. | Create a unique index and translate `DuplicateKeyError` to `409`. |
| 7 | Punch-in | Unknown employees, invalid statuses, and overnight attendance dates were not handled correctly. | Punch in an unknown employee or use an overnight shift after midnight. | Added request validation, employee checks, IST date calculation, and the unique daily-record index. |
| 8 | Attendance responses | Responses could expose MongoDB internals or return BSON datetimes instead of API timestamps. | Inspect employee and attendance responses. | Remove `_id` and serialize all instants as epoch milliseconds. |
| 9 | Attendance listing | The starter loaded and sorted the complete collection in Python. | Run the endpoint against a large collection. | Move filtering, sorting, pagination, and counting into MongoDB. |
| 10 | Missing functionality | Punch-out, regularization, analytics, trend, leaderboard, and explain routes were absent. | Compare the registered routes with `openapi.yaml`. | Implemented the complete documented API in `app/main.py`. |

The two-collection data model and customer-supplied `emp_code` were already
consistent with the assignment, so I kept them. I also kept the natural
attendance key of `(emp_code, date)` rather than introducing an API-facing
attendance ID.
