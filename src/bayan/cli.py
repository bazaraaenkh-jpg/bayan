"""CLI: python -m bayan.cli <command>

Командууд:
  parse <file.xlsx>      — хуулга боловсруулж тайлан хэвлэнэ (demo компаниар)
  registry               — бүртгэлтэй descriptor-уудыг жагсаана
"""

from __future__ import annotations

import sys
from pathlib import Path

from .amounts import format_minor
from .coa_seed import seed_company
from .db import make_session
from .pipeline import PipelineError, process_file
from .registry import load_registry


def cmd_registry() -> None:
    for d in load_registry():
        print(f"  {d.id:40s} [{d.status}] {d.bank}/{d.channel}/{d.file_type}")


def cmd_parse(file_path: str) -> None:
    session = make_session("sqlite:///:memory:")
    company = seed_company(session, "CLI Demo")
    try:
        report = process_file(session, company.id, Path(file_path))
    except PipelineError as e:
        print(f"АЛДАА: {e}")
        sys.exit(1)

    print(f"Descriptor : {report.descriptor_id}")
    print(f"Гүйлгээ    : {report.txn_count}")
    print(f"Gate       : {'✓ ДАВЛАА' if report.gate_ok else '✗ ДАВСАНГҮЙ'}")
    for issue in report.issues:
        row = f"мөр {issue['row']}" if issue["row"] else "ерөнхий"
        print(f"  [{issue['check']}] {row}: {issue['detail']}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "registry":
        cmd_registry()
    elif cmd == "parse" and len(sys.argv) > 2:
        cmd_parse(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
