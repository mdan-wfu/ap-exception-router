"""CLI entry point. Honors --invoice_path, --batch, --live, --replay."""
import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="AP Exception Router")
    parser.add_argument("--invoice_path", help="Path to a single invoice file")
    parser.add_argument("--batch", action="store_true", help="Process the full corpus")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", action="store_true", help="Hit the real LLM API")
    mode_group.add_argument("--replay", action="store_true", help="Replay from cassettes only")

    args = parser.parse_args()

    # Resolve LLM mode: CLI flag > env > "auto"
    if args.live:
        mode = "live"
    elif args.replay:
        mode = "replay"
    else:
        mode = os.environ.get("LLM_MODE", "auto")
    os.environ["LLM_MODE"] = mode

    raise NotImplementedError("Pipeline not yet implemented — Phase 3+")


if __name__ == "__main__":
    main()
