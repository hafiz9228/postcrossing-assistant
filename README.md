# Postcrossing Assistant

## Setup
1. Copy this project folder anywhere on your computer.
2. Install Python 3.10 or newer.
3. Open Command Prompt in this folder.
4. Install dependencies:

   pip install -r requirements.txt

5. Copy `.env.example` to `.env`
6. Edit `.env` and fill in:
   - `LLM_API_KEY`
   - `LLM_BASE_URL`
   - `LLM_MODEL`

## Run
Double-click `Launch Assistant.bat`

or run:

python -m streamlit run app.py

## Notes
- The app will automatically create the database tables on first run.
- Images are stored in the `images` folder.
- The database file is `postcards.db`.