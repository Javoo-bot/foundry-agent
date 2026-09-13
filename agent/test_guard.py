"""The SQL guard is a security control, so it gets tested like one.

Evaluation case R02 asks the agent to drop a table. It has to fail at the tool
boundary rather than because the model was polite enough to decline, so these
run in CI on every commit and a failure here fails the build.

    python -m pytest gishub/agent/test_guard.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.fleet_agent import GuardRejection, guard  # noqa: E402

ALLOWED = [
    ("SELECT 1", "a plain read"),
    ("  with t as (select 1) select * from t  ", "a CTE"),
    ("SELECT * FROM gishub.fleet.vehicles -- create table x", "a keyword inside a comment"),
    ("SELECT count(*) FROM t;", "one statement with a trailing semicolon"),
    ("SELECT * FROM t; --", "a trailing semicolon and an empty comment"),
    ("SELECT /* drop */ 1", "a keyword inside a block comment"),
    ("SELECT 1 -- ; DROP TABLE x", "a stacked statement that is commented out"),
]

BLOCKED = [
    ("DROP TABLE warranty_claims", "a bare drop"),
    ("SELECT 1; DROP TABLE t", "a stacked statement"),
    ("SELECT 1 --\n; DROP TABLE t", "a stacked statement revealed by stripping a comment"),
    ("SELECT 1 /*;*/ ; DROP TABLE t", "a stacked statement revealed by stripping a block comment"),
    ("DELETE FROM vehicles", "a delete"),
    ("INSERT INTO t VALUES (1)", "an insert"),
    ("UPDATE t SET a = 1", "an update"),
    ("CREATE TABLE x AS SELECT 1", "a create"),
    ("GRANT SELECT ON t TO u", "a grant"),
    ("", "an empty statement"),
    ("   ", "whitespace only"),
]


@pytest.mark.parametrize("sql,label", ALLOWED, ids=[l for _, l in ALLOWED])
def test_reads_are_allowed(sql, label):
    assert guard(sql)


@pytest.mark.parametrize("sql,label", BLOCKED, ids=[l for _, l in BLOCKED])
def test_everything_else_is_refused(sql, label):
    with pytest.raises(GuardRejection):
        guard(sql)
