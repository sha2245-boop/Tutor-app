# TutorBook

Attendance and per-class fee tracking for private tutors.

Mark attendance with one tap. At month end, TutorBook works out each family's
bill (absences, holidays, make-up classes, carried-over dues) and writes a
ready-to-send WhatsApp reminder for every pending payment.

## Features
- Batches and students, with a fee per class
- One-tap attendance: Present, Absent, Excused (make-up owed)
- Holidays are never billed; make-up sessions are billed when attended
- Monthly bills with carry-forward of unpaid amounts
- Courses with syllabus topics and progress, linked to batches
- Teacher feedback per student, sendable to parents on WhatsApp
- Study-material library (files or links) sorted by subject and batch
- WhatsApp reminder with UPI ID, plus CSV export and database backup

## Tech
Vanilla HTML/CSS/JavaScript frontend, Python (Flask) backend, SQLite database.

## Run
```
pip install -r requirements.txt
python app.py
```
Open http://localhost:8000

Optional password: set `TUTORBOOK_PASSWORD` before starting. Leave it unset for no login.

## Layout
```
app.py            Flask server, SQLite storage, billing export
static/index.html The whole frontend
```

## Status
Working single-user prototype. Next: tutor accounts, hosting, installable mobile version.
