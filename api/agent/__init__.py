"""Pear's Google ADK agents. Only one today: the /v1/ask Workflow in agent/graph.py.

Importing this package is cheap; google.adk itself (about 1 s of imports) loads on the
first agent.graph.run_ask() / agent.graph.warm() call. See agent/README.md.
"""
