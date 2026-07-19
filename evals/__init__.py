"""Offline evaluation harness for the agent's classification and extraction.

Runs the same graph nodes the production pipeline uses against a labeled dataset
and reports quality metrics. The identical harness measures the sandbox model
(deterministic, used as a CI regression gate) or a real model — swap via
``LLM_MODE``. See ``python -m evals --help``.
"""
