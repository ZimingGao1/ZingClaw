"""Trace, replay, and scorecard evaluation helpers."""

from zingclaw.evaluation.questions import (
    TraceQuestion,
    TraceQuestionRunResult,
    TraceQuestionSet,
    load_question_set,
    run_question_set,
    starter_question_set,
    write_starter_question_set,
)
from zingclaw.evaluation.curation import CuratedTrace, CurationResult, curate_traces
from zingclaw.evaluation.dataset import (
    ReplayCaseDatasetItem,
    ReplayDatasetResult,
    enrich_replay_case_payload,
    organize_replay_dataset,
)
from zingclaw.evaluation.fixtures import (
    TaskWorkspace,
    WorkspaceDiff,
    WorkspaceEntry,
    WorkspaceSnapshot,
    diff_snapshots,
    prepare_task_workspace,
    snapshot_workspace,
)
from zingclaw.evaluation.replay import ReplayCase, ReplayRunResult, run_replay_case
from zingclaw.evaluation.scorecard import CheckResult, Scorecard, evaluate_trace
from zingclaw.evaluation.suite import (
    SuiteCaseResult,
    SuiteRunResult,
    discover_replay_cases,
    run_replay_suite,
)
from zingclaw.evaluation.task_runner import (
    TaskRunCaseResult,
    TaskRunSuiteResult,
    run_task_contract,
    run_task_suite,
)
from zingclaw.evaluation.task_contract import (
    ArtifactSpec,
    TaskContract,
    VerifierSpec,
    discover_task_contracts,
    load_task_contract,
)
from zingclaw.evaluation.trace import (
    TRACE_DISABLED_METADATA_KEY,
    RuntimeTraceCapture,
    TraceRecorder,
    TraceStore,
)
from zingclaw.evaluation.verifiers import (
    VerificationCheck,
    VerificationReport,
    evaluate_task_contract,
)

__all__ = [
    "CheckResult",
    "CuratedTrace",
    "ArtifactSpec",
    "CurationResult",
    "ReplayCase",
    "ReplayCaseDatasetItem",
    "ReplayDatasetResult",
    "ReplayRunResult",
    "Scorecard",
    "RuntimeTraceCapture",
    "SuiteCaseResult",
    "SuiteRunResult",
    "TRACE_DISABLED_METADATA_KEY",
    "TaskContract",
    "TaskRunCaseResult",
    "TaskRunSuiteResult",
    "TaskWorkspace",
    "TraceQuestion",
    "TraceQuestionRunResult",
    "TraceQuestionSet",
    "TraceRecorder",
    "TraceStore",
    "VerificationCheck",
    "VerificationReport",
    "VerifierSpec",
    "WorkspaceDiff",
    "WorkspaceEntry",
    "WorkspaceSnapshot",
    "curate_traces",
    "diff_snapshots",
    "discover_task_contracts",
    "discover_replay_cases",
    "enrich_replay_case_payload",
    "evaluate_trace",
    "evaluate_task_contract",
    "load_task_contract",
    "load_question_set",
    "organize_replay_dataset",
    "prepare_task_workspace",
    "run_replay_case",
    "run_replay_suite",
    "run_task_contract",
    "run_task_suite",
    "run_question_set",
    "snapshot_workspace",
    "starter_question_set",
    "write_starter_question_set",
]
