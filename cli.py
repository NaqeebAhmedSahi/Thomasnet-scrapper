import sys

try:
    from app.cli import main
except ImportError as exc:
    sys.stderr.write(
        "Dependencies are missing. From the project folder run:\n"
        "  python3 -m venv .venv\n"
        "  source .venv/bin/activate\n"
        "  pip install -r requirements.txt\n\n"
        f"Import error: {exc}\n"
    )
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
