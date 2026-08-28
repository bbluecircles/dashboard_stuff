# Arizona provider directory

Python mart first. FastAPI later wraps the same functions (`get_provider`, `run_phase1`).

data dict => https://d3low7qefzg7gr.cloudfront.net/myfantasyai/assets/data-dict.csv  
snapshot => https://d3low7qefzg7gr.cloudfront.net/myfantasyai/assets/db_snapshot.json

## Phase 1

Identity spine from `az.physician` (Type 1 NPIs) plus CMS staging tables.

```
pip install -r requirements.txt
python -m pytest
python -m provider_directory.cli phase1
python -m provider_directory.cli phase1 --download --skip-nppes
python -m provider_directory.cli get 1234567893
```

`--download` pulls current CMS PDC CSVs (and the ~1.1 GB NPPES monthly zip unless `--skip-nppes`). Files land in `data/cms/` and are reused on the next run.

Same DB env as `db_snapshot.py`: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`. Optional: `PD_MART_DB` (default `az_pd`), `PD_CLAIMS_DB` (`az`), `PD_LOOKUP_DB` (`azal`).

## Phase 2

Activity, panel, and top codes for `period_code` 202308–202407. Reads `az.pat_dt` / `azal.diagnosis` / `azal.procd`. Writes only `az_pd`.

Wait until Phase 1 `--download` has finished (it updates `pd_provider`). Then:

```
python -m provider_directory.cli phase2
python -m provider_directory.cli get --last-name Smith --limit 3
```

This scan is large (12 months of `pat_dt`). Galera cannot replicate one giant transaction (`Maximum writeset size exceeded`), so Phase 2 commits by month and hash bucket. You should see `phase2 window YYYYMM bucket N` lines as it goes.
