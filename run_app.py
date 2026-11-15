from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from ds100.smoke import main
        raise SystemExit(main(Path(sys.argv[2])))
    else:
        from ds100.app import main
        raise SystemExit(main())
