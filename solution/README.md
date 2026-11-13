# Assignment A solution

Interaction ledger and reporting pipeline. Run these commands from the repository root.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2
python -m solution report --out reports
pytest
```

The SQLite file defaults to `au_assignment.sqlite` in the working directory. Pass `--db` to put it somewhere else. `data/`, `reports/`, and `*.sqlite*` are gitignored.
