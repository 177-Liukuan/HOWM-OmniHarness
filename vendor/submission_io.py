#!/usr/bin/env python3
"""Locked, durable JSONL submission writes for the exact question IDs in a frozen manifest.

All cooperating writers must use this helper and leave the output inode in place.
No command rewrites completed records. Tail repair is explicit and archives the
discarded bytes durably before truncating an invalid unterminated final record.
"""

import argparse
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path


DEFAULT_MANIFEST = Path('/storage1/HOWM-LAB-Project/evaluations/'
                        'codex-gpt6-factor-agent-265-20260929/sampling_manifest.json')


class SubmissionError(ValueError):
    """Invalid input or an existing submission that cannot safely be changed."""


def expected_ids(manifest=DEFAULT_MANIFEST):
    """Read exact raw IDs; never infer membership from a numeric interval."""
    try:
        value = json.loads(Path(manifest).read_text(encoding="utf-8"),
                           object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubmissionError(f"invalid manifest JSON: {exc}") from exc
    ids = value.get("combined_question_ids") if isinstance(value, dict) else None
    if not isinstance(ids, list) or not ids:
        raise SubmissionError("manifest combined_question_ids must be a nonempty list")
    for qid in ids:
        if (not isinstance(qid, str) or not qid.isascii() or not qid.isdecimal()
                or int(qid) < 1 or qid != str(int(qid)).zfill(3)):
            raise SubmissionError("manifest question IDs must be canonical raw ID strings")
    if len(ids) != len(set(ids)):
        raise SubmissionError("manifest contains duplicate question IDs")
    return set(ids)


def check_record(record, expected):
    if not isinstance(record, dict) or set(record) != {"question_id", "answer"}:
        raise SubmissionError("record must have exactly question_id and answer")
    qid, answer = record["question_id"], record["answer"]
    if not isinstance(qid, str) or qid not in expected:
        raise SubmissionError("question_id must be a canonical string listed in the frozen manifest")
    if type(answer) is not int or not 1 <= answer <= 30:
        raise SubmissionError("answer must be an integer from 1 to 30, excluding bool")
    return record


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise SubmissionError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def parse_record(raw, expected):
    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubmissionError(f"invalid UTF-8 JSON: {exc}") from exc
    return check_record(record, expected)


def inspect_bytes(data, manifest=DEFAULT_MANIFEST):
    expected = expected_ids(manifest)
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    seen, first_lines, duplicates, invalid = {}, {}, [], []
    for number, raw in enumerate(lines, 1):
        try:
            record = parse_record(raw, expected)
        except SubmissionError as exc:
            invalid.append({"line": number, "error": str(exc)})
            continue
        qid, answer = record["question_id"], record["answer"]
        if qid in seen:
            duplicates.append({"question_id": qid, "line": number,
                               "first_line": first_lines[qid],
                               "conflicting": seen[qid] != answer})
        else:
            seen[qid], first_lines[qid] = answer, number
    missing = sorted(expected - seen.keys())
    return {
        "expected": len(expected),
        "lines": len(lines),
        "completed": len(seen),
        "missing": missing,
        "duplicates": duplicates,
        "invalid": invalid,
        "unterminated_tail": bool(data and not data.endswith(b"\n")),
        "complete": not missing and not duplicates and not invalid,
    }, seen


def require_clean(summary):
    if summary["invalid"]:
        issue = summary["invalid"][0]
        raise SubmissionError(f"invalid existing line {issue['line']}: {issue['error']}; "
                              "inspect the file, or explicitly repair-tail for a torn final line")
    if summary["duplicates"]:
        raise SubmissionError("existing duplicate question_id records; file left unchanged")


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_answer(output, question_id, answer, manifest=DEFAULT_MANIFEST):
    expected = expected_ids(manifest)
    record = check_record({"question_id": question_id, "answer": answer}, expected)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # a+b neither truncates nor overwrites, and flock covers the entire transaction.
    with output.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        data = handle.read()
        summary, seen = inspect_bytes(data, manifest)
        require_clean(summary)
        if question_id in seen:
            if seen[question_id] != answer:
                raise SubmissionError(f"conflicting answer for {question_id}: "
                                      f"existing={seen[question_id]}, requested={answer}")
            return {"status": "existing", **record}
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        # A valid unterminated last record is preserved, with only its delimiter added.
        handle.write((b"\n" if summary["unterminated_tail"] else b"") + encoded)
        handle.flush()
        os.fsync(handle.fileno())
        sync_directory(output.parent)
        return {"status": "appended", **record}


def validate_submission(output, manifest=DEFAULT_MANIFEST):
    expected_ids(manifest)
    try:
        handle = Path(output).open("rb")
    except FileNotFoundError:
        summary, _ = inspect_bytes(b"", manifest)
        return {**summary, "exists": False}
    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        summary, _ = inspect_bytes(handle.read(), manifest)
        return {**summary, "exists": True}


def repair_tail(output, manifest=DEFAULT_MANIFEST):
    expected = expected_ids(manifest)
    output = Path(output)
    with output.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        data = handle.read()
        if not data or data.endswith(b"\n"):
            summary, _ = inspect_bytes(data, manifest)
            require_clean(summary)
            return {"status": "unchanged", "removed_bytes": 0, "archive": None}
        split = data.rfind(b"\n") + 1
        prefix, tail = data[:split], data[split:]
        summary, _ = inspect_bytes(prefix, manifest)
        require_clean(summary)
        try:
            parse_record(tail, expected)
        except SubmissionError:
            # Keep a recoverable, byte-exact archive before any destructive operation.
            with tempfile.NamedTemporaryFile(mode="wb", dir=output.parent,
                                             prefix=output.name + ".tail-",
                                             delete=False) as archive:
                archive.write(tail)
                archive.flush()
                os.fsync(archive.fileno())
                archive_path = Path(archive.name)
            sync_directory(output.parent)
            handle.truncate(split)
            handle.flush()
            os.fsync(handle.fileno())
            return {"status": "repaired", "removed_bytes": len(tail),
                    "archive": str(archive_path.resolve())}
        # Valid records, including duplicate records, are never discarded by repair.
        summary, _ = inspect_bytes(data, manifest)
        require_clean(summary)
        return {"status": "unchanged", "removed_bytes": 0, "archive": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("append", "validate", "repair-tail"):
        command = commands.add_parser(name)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                             help="frozen sampling manifest containing combined_question_ids")
        if name == "append":
            command.add_argument("--question-id", required=True)
            command.add_argument("--answer", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "append":
            result = append_answer(args.output, args.question_id, args.answer, args.manifest)
        elif args.command == "repair-tail":
            result = repair_tail(args.output, args.manifest)
        else:
            result = validate_submission(args.output, args.manifest)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if args.command == "validate" and not result["complete"] else 0
    except (SubmissionError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
