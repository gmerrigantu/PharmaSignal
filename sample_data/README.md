# Bundled demo dataset

`gold/*.parquet` here is a **deterministic synthetic** dataset (seed = 42) produced by
`python -m pharmasignal.pipeline.generate_demo`. It lets reviewers run the dashboard
and tests with **no network and no cloud credentials**.

⚠️ **This is NOT real FAERS data.** Numbers are synthetic but use the real modeling
functions, and the elevated GLP-1 GI signals (gastroparesis, ileus, pancreatitis,
cholelithiasis) are seeded to mirror well-known real-world patterns for a realistic
demo. For live data run `make pipeline`.

Regenerate with: `make demo`.
