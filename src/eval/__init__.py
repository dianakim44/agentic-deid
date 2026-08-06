"""Evaluation.

`sealed_log` records every access to a sealed fold; `run_sealed_eval` is the only
module the loader's seal gate accepts as a caller. Nothing else in this package
may import a sealed fold, and nothing here may be imported to make that easier.
"""
