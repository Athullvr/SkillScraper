import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sample command-line tool")
    parser.add_argument("input", help="Input file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return 0 if args.input else 1


if __name__ == "__main__":
    raise SystemExit(main())
