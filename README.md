#launch app
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
#update dependencies
pip freeze > requirements.txt
#watch db locally
#psql -h localhost -U postgres -d DriveClon
