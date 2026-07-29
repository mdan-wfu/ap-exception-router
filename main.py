"""CLI entry point. Honors --invoice_path, --batch, --live, --replay."""
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="AP Exception Router")
    parser.add_argument("--invoice_path", help="Path to a single invoice file")
    parser.add_argument("--batch", action="store_true", help="Process full corpus")
    parser.add_argument("--live", action="store_true", help="Hit the real LLM API")
    parser.add_argument("--replay", action="store_true", help="Replay from cassettes")
    args = parser.parse_args()

    raise NotImplementedError("Pipeline not yet implemented — Phase 1+")


if __name__ == "__main__":
    main()
