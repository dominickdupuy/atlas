"""Identifier newtypes shared across contexts."""

from __future__ import annotations

import uuid
from typing import NewType

JobId = NewType("JobId", str)
RunId = NewType("RunId", str)
ApprovalId = NewType("ApprovalId", str)
EntryId = NewType("EntryId", str)


def new_run_id() -> RunId:
    return RunId(str(uuid.uuid4()))


def new_approval_id() -> ApprovalId:
    return ApprovalId(str(uuid.uuid4()))


def new_entry_id() -> EntryId:
    return EntryId(str(uuid.uuid4()))
