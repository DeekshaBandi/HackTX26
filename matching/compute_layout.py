"""
Computes 3D positions (x, y, z) for every student ONCE and saves them in Tiger,
so the API never has to download embeddings during the demo.

Also saves layout.json (the PCA projection), so live callers can be placed
in the same 3D space from just their own vector.

Run once after load_tiger.py (and again any time you reload the fake students):
    python compute_layout.py
"""
import json
import time

import numpy as np
from sklearn.decomposition import PCA

from common import tiger_connect

conn = tiger_connect()
conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS x REAL, "
             "ADD COLUMN IF NOT EXISTS y REAL, ADD COLUMN IF NOT EXISTS z REAL")

t0 = time.time()
print("Downloading fake student embeddings (one time, can take a minute on slow Wi-Fi)...")
rows = conn.execute(
    "SELECT student_id, embedding_sf FROM students WHERE is_synthetic AND embedding_sf IS NOT NULL"
).fetchall()
print(f"  got {len(rows)} in {time.time() - t0:.1f}s")

ids = [r[0] for r in rows]
X = np.stack([np.asarray(r[1], dtype=float) for r in rows])
pca = PCA(n_components=3, random_state=0).fit(X)
pts = pca.transform(X)
scale = np.abs(pts).max(axis=0)
pts = pts / scale

with open("layout.json", "w") as f:
    json.dump({"mean": pca.mean_.tolist(), "components": pca.components_.tolist(),
               "scale": scale.tolist()}, f)

print("Saving x, y, z to Tiger...")
with conn.cursor() as cur:
    cur.executemany(
        "UPDATE students SET x = %s, y = %s, z = %s WHERE student_id = %s",
        [(float(p[0]), float(p[1]), float(p[2]), sid) for sid, p in zip(ids, pts)],
    )
print(f"Done in {time.time() - t0:.1f}s. Saved layout.json too (commit it, it's not secret).")