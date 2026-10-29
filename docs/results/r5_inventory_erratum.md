# Erratum to the R5 inventory (found while preparing R6)

**What was wrong.** `ml/r5_inventory.py` checked file existence and size with a Python `urllib` HTTP HEAD request. The server rejects the default `Python-urllib` User-Agent on HEAD (a browser User-Agent returns 200 and the size; `curl -I` returns 200), so every such check recorded `404` and no size. The affected fields in `docs/results/r5_inventory.json` are `capture_files_needed` and `benign_exposure.csv_head`. They are wrong: the files exist (the nine R5 test days and the validation days were downloaded and used, and the 2018 files return 200).

**What it did not affect.** The R5 gate counts episodes from the annotation schedule, the device table and the date listings from the documentation pages, none of which used the HEAD helper. The gate result (15 eligible of 30 required), the exclusion reasons, the validation and test false-alarm results and the frozen manifests are unchanged.

**What was not done.** The recorded `404` values were in the output but I did not flag them when reporting the inventory; they should have been questioned, because the 2016 files in the same list had been downloaded. The frozen files `docs/results/r5_inventory.json` and `ml/r5_inventory.py` are left as frozen. R6 (`ml/r6_audit.py`) sends a browser User-Agent, and its size and availability fields are the corrected ones.
