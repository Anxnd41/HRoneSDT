# Employee Attendance and Analytics API

This project is a small FastAPI service for managing employee attendance. It uses
MongoDB for storage and includes a browser dashboard so the data can be reviewed
without working directly with JSON requests.

The API follows the contract in [`openapi.yaml`](./openapi.yaml). All application
code, including the dashboard, is in [`app/main.py`](./app/main.py).

## What is included

- Employee creation and listing
- Punch-in and punch-out
- Attendance filtering and pagination
- Manual attendance corrections with an audit history
- Monthly employee and department reports
- Late-coming leaderboard
- Department attendance trends
- A MongoDB explain endpoint
- A simple dashboard at `/`
- Swagger documentation at `/docs`

The application handles IST attendance dates, overnight shifts, epoch-millisecond
timestamps, half-day calculations, and the required MongoDB indexes.

## Requirements

- Python 3.10 or newer
- MongoDB 6.0 or newer
- Docker is optional, but is the easiest way to run MongoDB locally

## Setup on Windows

Open PowerShell in this folder:

```powershell
cd C:\Users\anand\Downloads\SDT_candidate_kit\candidate_kit
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell prevents activation, run this once in the current terminal and
then activate the environment again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Start MongoDB

With Docker Desktop running, start MongoDB with:

```powershell
docker run -d --name attendance-mongo -p 27017:27017 mongo:7
```

If the container already exists, use:

```powershell
docker start attendance-mongo
```

The application uses these defaults:

```text
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

To set them explicitly in PowerShell:

```powershell
$env:MONGO_URI="mongodb://localhost:27017"
$env:MONGO_DB="attendance_db"
```

## Load the sample data

Run the seed script after MongoDB is available:

```powershell
python sample_seed.py
```

The script loads the sample files from [`sample_data/`](./sample_data/). It is
safe to use when setting up the project, but running it again will reload the
sample collections.

## Run the service

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open the dashboard:

<http://127.0.0.1:8000/>

Other useful URLs:

- <http://127.0.0.1:8000/docs> - Swagger UI
- <http://127.0.0.1:8000/redoc> - ReDoc
- <http://127.0.0.1:8000/health> - database readiness check

The dashboard is served by FastAPI, so there is no separate Node.js project or
frontend build step.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `attendance_db` | MongoDB database name |

Do not commit a `.env` file or credentials to the repository.

## Submission notes

The repository should include the application source, sample data, API contract,
data model, review notes, decisions, and requirements file. Do not include
`.venv`, `.env`, `__pycache__`, database files, or local logs.
