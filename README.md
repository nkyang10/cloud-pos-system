# cloud-pos-system

A tiny POS (point-of-sale) system for tracking stock items: view the catalog
and add new items from a single server-rendered HTML page.

- Python 3.8+, standard library only — no `pip install`.
- Items persist in a local SQLite file (`stock.db`).

## Run

    python run.py

Open <http://127.0.0.1:8000> in a browser. If port 8000 is taken, override it
with the `PORT` environment variable:

    PORT=9000 python run.py

## Test

    python -m unittest discover -s tests -v
